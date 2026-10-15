"""Contrato de geração e capacidades públicas dos modelos do OpenRouter.

A consulta de capacidades é explícita e nunca usa uma credencial. A geração
reutiliza o transporte do Lingo e seus limites de repetição de falhas transitórias.
"""

from __future__ import annotations

import json
from threading import Lock
from time import monotonic
from typing import Protocol, cast, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from api_narrativas import ErroAPINarrativa, _contexto_https
import geracao_paragrafo as _transporte


ErroGeracao = _transporte.ErroGeracao

_ENDPOINT_MODELOS = "https://openrouter.ai/api/v1/models"
_TIMEOUT_CATALOGO = 10
_LIMITE_CATALOGO_BYTES = 8 * 1024 * 1024
_TTL_CATALOGO = 300
_cache_catalogo: dict[str, bool] = {}
_cache_expira = 0.0
_cache_lock = Lock()

_AVISO_DESCONHECIDO = (
    "O suporte à temperatura deste modelo ainda não foi confirmado. "
    "A temperatura não será enviada ao OpenRouter."
)
_AVISO_SEM_SUPORTE = (
    "Este modelo não declara suporte à temperatura no catálogo do OpenRouter. "
    "A temperatura não será enviada."
)
_AVISO_FORA_CATALOGO = (
    "O modelo não foi encontrado no catálogo do OpenRouter. "
    "A temperatura não será enviada."
)
_AVISO_CONSULTA = (
    "Não foi possível consultar as capacidades no catálogo do OpenRouter. "
    "A temperatura não será enviada."
)


class _SemRedirecionamento(HTTPRedirectHandler):
    """Mantenha a consulta de capacidades no destino público fixo."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _capacidade(suportada: bool, aviso: str | None = None) -> dict:
    # Cada chamada recebe estruturas próprias, sem poder alterar o cache.
    return {
        "temperatura": {
            "suportada": suportada,
            "min": 0.0,
            "max": 1.2,
            "padrao": 0.8,
        },
        "avisos": [aviso] if aviso else [],
    }


def _ler_catalogo() -> dict[str, bool]:
    """Busque apenas metadados públicos, com TLS e tamanho verificados."""
    pedido = Request(_ENDPOINT_MODELOS, headers={"Accept": "application/json"}, method="GET")
    cliente = build_opener(_SemRedirecionamento(), HTTPSHandler(context=_contexto_https()))
    with cliente.open(pedido, timeout=_TIMEOUT_CATALOGO) as resposta:
        dados = resposta.read(_LIMITE_CATALOGO_BYTES + 1)
    if len(dados) > _LIMITE_CATALOGO_BYTES:
        raise ValueError("Catálogo acima do limite permitido.")
    resultado = json.loads(dados)
    if not isinstance(resultado, dict) or not isinstance(resultado.get("data"), list):
        raise ValueError("Formato de catálogo inválido.")
    catalogo = {}
    for registro in resultado["data"]:
        if not isinstance(registro, dict):
            continue
        identificador = registro.get("id")
        try:
            modelo = _transporte._validar_identificador(identificador, "modelo", _transporte.LIMITE_MODELO)
        except ErroGeracao:
            continue
        parametros = registro.get("supported_parameters")
        catalogo[modelo] = isinstance(parametros, list) and "temperature" in parametros
    return catalogo


def capacidades_modelo(modelo: str, consultar: bool = False) -> dict:
    """Informe suporte exato à temperatura, usando cache público por cinco minutos.

    ``consultar=False`` nunca acessa a rede. Um modelo desconhecido ou uma
    consulta malsucedida desativa a temperatura e devolve um aviso seguro.
    """
    modelo = _transporte._validar_identificador(modelo, "modelo", _transporte.LIMITE_MODELO)
    if type(consultar) is not bool:
        raise ErroGeracao("A opção de consulta de capacidades deve ser verdadeira ou falsa.")
    global _cache_catalogo, _cache_expira
    with _cache_lock:
        vigente = monotonic() < _cache_expira
        if not vigente and consultar:
            try:
                catalogo = _ler_catalogo()
            except HTTPError as erro:
                # O corpo do erro pode incluir informações privadas: não o leia.
                erro.close()
                return _capacidade(False, _AVISO_CONSULTA)
            except (ErroAPINarrativa, URLError, OSError, ValueError, RecursionError):
                return _capacidade(False, _AVISO_CONSULTA)
            _cache_catalogo = catalogo
            _cache_expira = monotonic() + _TTL_CATALOGO
            vigente = True
        if not vigente:
            return _capacidade(False, _AVISO_DESCONHECIDO)
        if modelo not in _cache_catalogo:
            return _capacidade(False, _AVISO_FORA_CATALOGO)
        suportada = _cache_catalogo[modelo]
        return _capacidade(suportada, None if suportada else _AVISO_SEM_SUPORTE)


@runtime_checkable
class LLMClient(Protocol):
    """Interface mínima para gerar um parágrafo a partir de um prompt."""

    def gerar(self, prompt: str, max_tokens: int, temperatura: float | None = None) -> str:
        ...


class OpenRouterClient:
    """Cliente com credencial somente em memória e temperatura conservadora."""

    def __init__(self, modelo: str, chave_api: str):
        self.modelo = _transporte._validar_identificador(modelo, "modelo", _transporte.LIMITE_MODELO)
        self._chave_api = _transporte._validar_identificador(chave_api, "chave de API", _transporte.LIMITE_CHAVE)

    def gerar(self, prompt: str, max_tokens: int, temperatura: float | None = None) -> str:
        suportada = capacidades_modelo(self.modelo, consultar=False)["temperatura"]["suportada"]
        return cast(str, gerar_paragrafo(
            "openrouter",
            self.modelo,
            self._chave_api,
            prompt=prompt,
            max_tokens=max_tokens,
            temperatura=temperatura if suportada else None,
        ))


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
    """Preserve o acesso ao transporte compartilhado e seu retorno detalhado."""
    opcoes = {"max_tokens": max_tokens} if max_tokens is not None else {}
    return _transporte.gerar_paragrafo(
        provedor,
        modelo,
        chave_api,
        prompt=prompt,
        temperatura=temperatura,
        retorno_detalhado=retorno_detalhado,
        **opcoes,
    )


__all__ = ["ErroGeracao", "LLMClient", "OpenRouterClient", "capacidades_modelo", "gerar_paragrafo"]
