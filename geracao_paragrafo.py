"""Geração de um parágrafo com credenciais fornecidas somente para a chamada.

Não lê nem grava credenciais. Falhas transitórias têm até três envios no total,
incluindo eventual repetição sem temperatura quando o modelo a recusa.
"""

from __future__ import annotations

import json
import re
import socket
import ssl
import time
import unicodedata
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from api_narrativas import ErroAPINarrativa, _contexto_https


TIMEOUT_SEGUNDOS = 60
LIMITE_RESPOSTA_BYTES = 64 * 1024
LIMITE_MODELO = 200
LIMITE_CHAVE = 4096
LIMITE_PALAVRAS = 150
LIMITE_PALAVRAS_PERSONALIZADO = 600
LIMITE_CARACTERES_PARAGRAFO = 10000
LIMITE_PROMPT = 20000
LIMITE_PEDIDO_BYTES = 128 * 1024
LIMITE_ERRO_HTTP_BYTES = 16 * 1024
MAX_TENTATIVAS_REDE = 3

_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_PROMPT = (
    "Escreva uma narrativa fictícia sobre uma situação cotidiana, em português brasileiro. "
    "Gere exatamente um parágrafo, com no máximo 150 palavras, contendo personagens "
    "e um pequeno acontecimento. Entregue somente o texto do parágrafo, sem título, "
    "lista, comentários, explicações ou Markdown."
)
_RECUSA = re.compile(
    r"^(?:(?:desculp[ae](?:-me)?|sinto muito)[,!:.]?\s*)?"
    r"(?:mas\s+)?"
    r"(?:(?:eu\s+)?não\s+(?:posso|consigo)\s+"
    r"(?:ajudar|atender|gerar|fornecer|produzir|criar|cumprir)\b|"
    r"i\s+(?:cannot|can't|am unable to)\s+(?:help|assist|generate|provide|create)\b)",
    re.IGNORECASE,
)


class ErroGeracao(ValueError):
    """Erro cuja mensagem e código HTTP podem ser devolvidos pela aplicação."""

    def __init__(self, mensagem: str, status_code: int = 400):
        super().__init__(mensagem)
        self.status_code = status_code
        self.tipo = "api"
        self.texto_gerado = None
        self.metadados = {
            "tentativas_rede": 0,
            "avisos": [],
            "temperatura_enviada": None,
        }


class _SemRedirecionamento(HTTPRedirectHandler):
    """Nunca envie o cabeçalho de autenticação a um destino redirecionado."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validar_identificador(valor, nome: str, limite: int) -> str:
    mensagem = (
        f"O campo {nome} deve conter de 1 a {limite} caracteres visíveis, "
        "sem espaços internos ou caracteres de controle."
    )
    if (
        not isinstance(valor, str)
        or len(valor) > limite
        or any(unicodedata.category(letra).startswith("C") for letra in valor)
    ):
        raise ErroGeracao(mensagem)
    valor = valor.strip()
    # O OpenRouter usa IDs e chaves ASCII sem espaços.
    if not valor or any(not "!" <= letra <= "~" for letra in valor):
        raise ErroGeracao(mensagem)
    return valor


def _erro_http(codigo: int) -> ErroGeracao:
    erros = {
        400: ("O provedor recusou o pedido. Confira o modelo informado e a compatibilidade com geração de texto.", 400),
        401: ("A chave de API foi recusada. Confira a chave do OpenRouter nas configurações.", 401),
        402: ("O provedor informou saldo insuficiente. Confira os créditos da conta.", 402),
        403: ("O provedor não autorizou a geração. Confira as permissões da chave e o acesso ao modelo.", 403),
        404: ("O modelo não foi encontrado pelo provedor. Confira o identificador informado.", 400),
        429: ("O provedor atingiu um limite de uso ou cota. Confira sua conta e tente novamente mais tarde.", 429),
    }
    mensagem, status = erros.get(codigo, ("O provedor não conseguiu gerar o parágrafo. Tente novamente mais tarde.", 502))
    return ErroGeracao(mensagem, status)


def _erro_conexao(erro: Exception) -> ErroGeracao:
    motivo = erro.reason if isinstance(erro, URLError) else erro
    if isinstance(motivo, ssl.SSLCertVerificationError):
        return ErroGeracao("Não foi possível verificar o certificado HTTPS do provedor. Confira os certificados do Python.", 502)
    if isinstance(motivo, ssl.SSLError):
        return ErroGeracao("Não foi possível estabelecer uma conexão HTTPS segura com o provedor.", 502)
    if isinstance(motivo, (socket.timeout, TimeoutError)):
        return ErroGeracao("O provedor demorou demais para responder. Tente gerar o parágrafo novamente.", 504)
    return ErroGeracao("Não foi possível conectar ao provedor. Confira sua conexão e tente novamente.", 502)


def _recusa_amostragem(erro: HTTPError) -> bool:
    """Classifique um corpo pequeno sem expor ou guardar a mensagem externa."""
    try:
        corpo = erro.read(LIMITE_ERRO_HTTP_BYTES + 1)
        if not isinstance(corpo, bytes) or len(corpo) > LIMITE_ERRO_HTTP_BYTES:
            return False
        texto = corpo.decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return False
    return bool(re.search(r"\b(?:temperature|top[_ -]?p|top[_ -]?k|sampling|amostragem)\b", texto, re.IGNORECASE))


def _atraso_retry(erro: HTTPError | None, tentativa: int) -> float:
    padrao = 0.5 * 2 ** (tentativa - 1)
    valor = erro.headers.get("Retry-After") if erro is not None and erro.headers is not None else None
    if not isinstance(valor, str) or len(valor) > 100:
        return padrao
    try:
        atraso = float(valor)
    except ValueError:
        try:
            instante = parsedate_to_datetime(valor)
            atraso = instante.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return padrao
    if atraso != atraso:  # NaN não pode chegar a time.sleep.
        return padrao
    return max(0.0, min(5.0, atraso))


def _adicionar_tentativas(erro: ErroGeracao, tentativas: int, avisos: list[str], temperatura) -> ErroGeracao:
    erro.metadados = {
        "tentativas_rede": tentativas,
        "avisos": list(avisos),
        "temperatura_enviada": temperatura,
    }
    return erro


def _texto_resposta(resposta: object) -> str | None:
    """Recupere somente conteúdo de geração para inspeção segura pelo histórico."""
    if isinstance(resposta, dict):
        escolhas = resposta.get("choices")
        if isinstance(escolhas, list) and escolhas and isinstance(escolhas[0], dict):
            mensagem = escolhas[0].get("message")
            texto = mensagem.get("content") if isinstance(mensagem, dict) else None
            if isinstance(texto, str) and len(texto) <= LIMITE_CARACTERES_PARAGRAFO:
                return texto
    return None


def _extrair_paragrafo(resposta: object, *, limite_palavras: int = LIMITE_PALAVRAS) -> str:
    if not isinstance(resposta, dict) or resposta.get("error"):
        raise ErroGeracao("O provedor devolveu uma resposta inválida. Confira o modelo e tente novamente.", 502)
    escolhas = resposta.get("choices")
    if not isinstance(escolhas, list) or not escolhas or not isinstance(escolhas[0], dict):
        raise ErroGeracao("O provedor não devolveu um parágrafo. Tente novamente.", 502)
    escolha = escolhas[0]
    mensagem = escolha.get("message")
    if not isinstance(mensagem, dict):
        raise ErroGeracao("O provedor não devolveu um parágrafo. Tente novamente.", 502)
    if mensagem.get("refusal") or escolha.get("finish_reason") == "content_filter":
        raise ErroGeracao("O provedor recusou a geração. Tente outro modelo.", 502)
    if escolha.get("finish_reason") != "stop":
        raise ErroGeracao("O provedor não concluiu o parágrafo. Tente novamente ou escolha outro modelo.", 502)
    texto = mensagem.get("content")
    if not isinstance(texto, str) or not texto.strip():
        raise ErroGeracao("O provedor devolveu um texto vazio. Tente novamente.", 502)
    if len(texto) > LIMITE_CARACTERES_PARAGRAFO:
        raise ErroGeracao("O provedor devolveu um parágrafo grande demais. Tente novamente.", 502)
    texto = texto.strip().replace("\r\n", "\n").replace("\r", "\n")
    if re.search(r"\n[ \t]*\n|\u2029", texto):
        raise ErroGeracao("O provedor devolveu mais de um parágrafo. Tente novamente.", 502)
    if any(unicodedata.category(letra).startswith("C") and letra not in "\n\t" for letra in texto):
        raise ErroGeracao("O provedor devolveu caracteres inválidos. Tente novamente.", 502)
    if re.search(
        r"(?m)^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>)|`|\*\*|"
        r"</?[A-Za-z][^>]*>|!?\[[^\]]+\]\([^)]+\)|"
        r"(?<!\w)(?:\*[^*\n]+\*|_[^_\n]+_)(?!\w)",
        texto,
    ):
        raise ErroGeracao("O provedor devolveu formatação em vez de um parágrafo simples. Tente novamente.", 502)
    texto = " ".join(texto.split())
    if _RECUSA.search(texto):
        raise ErroGeracao("O provedor recusou a geração. Tente outro modelo.", 502)
    if len(texto.split()) > limite_palavras:
        raise ErroGeracao(f"O provedor devolveu mais de {limite_palavras} palavras. Tente novamente.", 502)
    return texto


def _texto_metadado(valor: object, chave_api: str, limite: int = 200) -> str | None:
    """Aceite somente identificadores pequenos e nunca ecoe a credencial."""
    if (
        not isinstance(valor, str)
        or not valor
        or len(valor) > limite
        or chave_api in valor
        or any(not "!" <= letra <= "~" for letra in valor)
    ):
        return None
    return valor


def _metadados_resposta(resposta: dict, modelo: str, chave_api: str) -> dict:
    uso = resposta.get("usage")
    if not isinstance(uso, dict):
        uso = {}
    contagens = {}
    for nome in ("prompt_tokens", "completion_tokens", "total_tokens"):
        valor = uso.get(nome)
        contagens[nome] = valor if type(valor) is int and 0 <= valor <= 1_000_000_000 else None
    return {
        "provedor": "openrouter",
        "modelo_solicitado": _texto_metadado(modelo, chave_api),
        "modelo_resposta": _texto_metadado(resposta.get("model"), chave_api),
        "id_resposta": _texto_metadado(resposta.get("id"), chave_api),
        "uso": contagens,
        # A extração já rejeitou qualquer conclusão diferente de stop.
        "finish_reason": "stop",
    }


def gerar_paragrafo(
    provedor: str,
    modelo: str,
    chave_api: str,
    *,
    prompt: str | None = None,
    temperatura: float | None = None,
    retorno_detalhado: bool = False,
    max_tokens: int | None = None,
) -> str | dict:
    """Gere um parágrafo via OpenRouter sem persistir a configuração.

    ``provedor`` deve ser ``openrouter``. Modelo e chave são obrigatórios.
    Sem ``prompt``, preserva o limite original de 150 palavras. Um prompt
    personalizado admite até 600 palavras e 10000 caracteres de saída.
    ``temperatura`` aceita números de 0 a 1.2. ``retorno_detalhado`` inclui
    somente identificadores e contagens de tokens validados, sem credenciais.
    Todos os erros previstos usam ``ErroGeracao`` com uma mensagem segura e
    ``status_code`` adequado para a rota HTTP.
    """
    if not isinstance(provedor, str) or provedor != "openrouter":
        raise ErroGeracao("A geração de parágrafos usa o OpenRouter.")
    modelo = _validar_identificador(modelo, "modelo", LIMITE_MODELO)
    chave_api = _validar_identificador(chave_api, "chave de API", LIMITE_CHAVE)
    if prompt is not None and (
        not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt) > LIMITE_PROMPT
        or any(unicodedata.category(letra).startswith("C") and letra not in "\n\r\t" for letra in prompt)
    ):
        raise ErroGeracao(f"O prompt deve conter texto válido de até {LIMITE_PROMPT} caracteres.")
    if temperatura is not None and (
        type(temperatura) not in (int, float) or not 0 <= temperatura <= 1.2
    ):
        raise ErroGeracao("A temperatura deve ser um número entre 0 e 1.2.")
    if type(retorno_detalhado) is not bool:
        raise ErroGeracao("A opção de retorno detalhado deve ser verdadeira ou falsa.")
    if max_tokens is not None and (type(max_tokens) is not int or not 1 <= max_tokens <= 16384):
        raise ErroGeracao("O limite de tokens deve ser um inteiro entre 1 e 16384.")
    corpo = {
        "model": modelo,
        "messages": [{"role": "user", "content": _PROMPT if prompt is None else prompt}],
        "max_tokens": max_tokens if max_tokens is not None else (2048 if prompt is None else 4096),
    }
    if temperatura is not None:
        corpo["temperature"] = temperatura
    try:
        # Compartilhe a confiança TLS já usada pelo projeto, inclusive no Mac:
        # CA nativas, Certifi quando disponível e bundles locais configurados.
        contexto = _contexto_https()
        cliente = build_opener(_SemRedirecionamento(), HTTPSHandler(context=contexto))
    except ErroAPINarrativa:
        raise ErroGeracao(
            "Não foi possível carregar os certificados HTTPS do Python. "
            "Confira a instalação e os caminhos de certificados configurados.",
            502,
        ) from None
    except (URLError, OSError, ValueError) as erro:
        raise _erro_conexao(erro) from None
    avisos = []
    fallback_utilizado = False
    for tentativa in range(1, MAX_TENTATIVAS_REDE + 1):
        dados_pedido = json.dumps(corpo, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(dados_pedido) > LIMITE_PEDIDO_BYTES:
            raise ErroGeracao("O prompt excedeu o tamanho permitido para geração.")
        pedido = Request(
            _ENDPOINT,
            data=dados_pedido,
            headers={"Authorization": f"Bearer {chave_api}", "Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with cliente.open(pedido, timeout=TIMEOUT_SEGUNDOS) as resposta:
                dados = resposta.read(LIMITE_RESPOSTA_BYTES + 1)
            break
        except HTTPError as erro:
            try:
                fallback = (
                    erro.code == 400
                    and "temperature" in corpo
                    and not fallback_utilizado
                    and tentativa < MAX_TENTATIVAS_REDE
                    and _recusa_amostragem(erro)
                )
                atraso = _atraso_retry(erro, tentativa)
            finally:
                erro.close()
            if fallback:
                corpo.pop("temperature")
                fallback_utilizado = True
                avisos.append("O modelo recusou parâmetros de amostragem. A geração foi repetida sem temperatura.")
                continue
            if (erro.code == 429 or 500 <= erro.code <= 599) and tentativa < MAX_TENTATIVAS_REDE:
                time.sleep(atraso)
                continue
            raise _adicionar_tentativas(_erro_http(erro.code), tentativa, avisos, corpo.get("temperature")) from None
        except (URLError, OSError, ValueError) as erro:
            motivo = erro.reason if isinstance(erro, URLError) else erro
            # Erros de confiança/configuração não se corrigem repetindo o envio.
            repetir = not isinstance(motivo, (ssl.SSLError, ValueError))
            if repetir and tentativa < MAX_TENTATIVAS_REDE:
                time.sleep(_atraso_retry(None, tentativa))
                continue
            raise _adicionar_tentativas(_erro_conexao(erro), tentativa, avisos, corpo.get("temperature")) from None
    metadados_tentativas = {
        "tentativas_rede": tentativa,
        "avisos": avisos,
        "temperatura_enviada": corpo.get("temperature"),
    }
    resultado = None
    try:
        if len(dados) > LIMITE_RESPOSTA_BYTES:
            raise ErroGeracao("O provedor devolveu uma resposta grande demais. Tente outro modelo.", 502)
        try:
            resultado = json.loads(dados)
        except (UnicodeError, ValueError, RecursionError):
            raise ErroGeracao("O provedor devolveu uma resposta ilegível. Tente novamente.", 502) from None
        texto = _extrair_paragrafo(
            resultado,
            limite_palavras=LIMITE_PALAVRAS if prompt is None else LIMITE_PALAVRAS_PERSONALIZADO,
        )
    except ErroGeracao as erro:
        erro.tipo = "conteudo"
        erro.texto_gerado = _texto_resposta(resultado)
        erro.metadados = metadados_tentativas
        raise
    if retorno_detalhado:
        return {"texto": texto, "metadados": {**_metadados_resposta(resultado, modelo, chave_api), **metadados_tentativas}}
    return texto
