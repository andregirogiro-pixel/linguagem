"""Leitura e validação do catálogo externo, sem credenciais ou efeitos de rede."""

from __future__ import annotations

import copy
import math
import os
import re
from pathlib import Path

import yaml
from dotenv import dotenv_values


class ErroParametros(ValueError):
    """Configuração ou solicitação de parâmetros inválida."""


_IDENTIFICADOR = re.compile(r"^[a-z][a-z0-9_]*$")
_ESPECIAIS = {"aleatorio", "variar"}
PADROES_CONTROLES = {
    "anti_repeticao": True,
    "n_historico": 50,
    "abertura_palavras": 6,
    "similaridade_ngramas": 3,
    "limiar_similaridade": 0.4,
    "max_tentativas": 2,
}
CAMINHO_PADRAO = Path(__file__).with_name("parameters.yaml")
CAMINHO_ENV = Path(__file__).resolve().parent.parent / ".env"


def humanizar(valor: str) -> str:
    return valor.replace("_", " ").capitalize()


def _numero(valor) -> bool:
    return isinstance(valor, (int, float)) and not isinstance(valor, bool) and math.isfinite(valor)


def _validar_configuracao(dados: object) -> dict:
    if not isinstance(dados, dict) or not isinstance(dados.get("eixos"), dict) or not dados["eixos"]:
        raise ErroParametros("A configuração precisa conter um mapa não vazio de eixos.")
    for eixo, especificacao in dados["eixos"].items():
        if not isinstance(eixo, str) or not _IDENTIFICADOR.fullmatch(eixo) or not isinstance(especificacao, dict):
            raise ErroParametros("Cada eixo deve ter um identificador ASCII e uma especificação.")
        valores = especificacao.get("valores")
        if (
            not isinstance(valores, list) or not valores
            or any(not isinstance(v, str) or not _IDENTIFICADOR.fullmatch(v) or v in _ESPECIAIS for v in valores)
            or len(set(valores)) != len(valores)
        ):
            raise ErroParametros(f"O eixo {eixo} precisa de valores únicos com identificadores ASCII.")
        camada = especificacao.get("camada", 1)
        if not isinstance(camada, int) or isinstance(camada, bool) or camada < 1:
            raise ErroParametros(f"A camada do eixo {eixo} deve ser um inteiro positivo.")
        pesos = especificacao.get("pesos", {})
        if not isinstance(pesos, dict) or set(pesos) - set(valores):
            raise ErroParametros(f"O eixo {eixo} contém pesos para valores desconhecidos.")
        if any(not _numero(p) or p < 0 for p in pesos.values()) or not any(pesos.get(v, 1) > 0 for v in valores):
            raise ErroParametros(f"Os pesos do eixo {eixo} devem ser não negativos, com pelo menos um peso positivo.")
    for bloco in ("rotulos", "instrucoes"):
        mapa = dados.get(bloco, {})
        if not isinstance(mapa, dict) or set(mapa) - set(dados["eixos"]):
            raise ErroParametros(f"O bloco {bloco} deve referenciar eixos existentes.")
        for eixo, valores in mapa.items():
            if not isinstance(valores, dict) or set(valores) - set(dados["eixos"][eixo]["valores"]):
                raise ErroParametros(f"O bloco {bloco} contém valores desconhecidos no eixo {eixo}.")
            if any(not isinstance(texto, str) or not texto.strip() for texto in valores.values()):
                raise ErroParametros(f"O bloco {bloco} precisa conter textos não vazios.")
    rotulos_eixos = dados.get("rotulos_eixos", {})
    if not isinstance(rotulos_eixos, dict) or set(rotulos_eixos) - set(dados["eixos"]) or any(
        not isinstance(texto, str) or not texto.strip() for texto in rotulos_eixos.values()
    ):
        raise ErroParametros("Os rótulos dos eixos devem conter textos para eixos existentes.")
    faixas = dados.get("tamanho_frases", {})
    if not isinstance(faixas, dict):
        raise ErroParametros("tamanho_frases deve conter faixas de quantidade de frases.")
    for faixa in faixas.values():
        if (
            not isinstance(faixa, list) or len(faixa) != 2
            or any(not isinstance(n, int) or isinstance(n, bool) or n < 1 for n in faixa)
            or faixa[0] > faixa[1]
        ):
            raise ErroParametros("Cada faixa de frases deve conter dois inteiros positivos em ordem.")
    if "eixo_tamanho" in dados and (
        not isinstance(dados["eixo_tamanho"], str) or dados["eixo_tamanho"] not in dados["eixos"]
    ):
        raise ErroParametros("eixo_tamanho precisa referenciar um eixo existente.")
    temperatura = dados.get("temperatura", {"min": 0.0, "max": 1.2, "padrao": 0.8, "jitter_lote": 0.15})
    if (
        not isinstance(temperatura, dict)
        or any(not _numero(temperatura.get(chave)) for chave in ("min", "max", "padrao", "jitter_lote"))
        or not 0 <= temperatura["min"] <= temperatura["padrao"] <= temperatura["max"] <= 2
        or temperatura["jitter_lote"] < 0
    ):
        raise ErroParametros("A configuração de temperatura é inválida.")
    regras = dados.get("regras", [])
    if not isinstance(regras, list):
        raise ErroParametros("As regras devem ser uma lista de incompatibilidades.")
    for regra in regras:
        if not isinstance(regra, dict) or not isinstance(regra.get("quando"), dict) or not regra["quando"]:
            raise ErroParametros("Cada regra precisa de um mapa não vazio em quando.")
        for eixo, restricao in regra["quando"].items():
            if eixo not in dados["eixos"]:
                raise ErroParametros("Uma regra referencia um eixo inexistente.")
            valores = restricao if isinstance(restricao, list) else [restricao]
            if not valores or any(not isinstance(v, str) or v not in dados["eixos"][eixo]["valores"] for v in valores):
                raise ErroParametros("Uma regra referencia um valor inexistente.")
        if "mensagem" in regra and not isinstance(regra["mensagem"], str):
            raise ErroParametros("A mensagem de uma regra deve ser texto.")
        if "acao" in regra:
            if regra["acao"] not in ("ajustar", "sortear", "bloquear"):
                raise ErroParametros("A ação de uma regra deve ser ajustar ou sortear.")
            alvo = regra.get("eixo")
            if not isinstance(alvo, str) or alvo not in dados["eixos"]:
                raise ErroParametros("O alvo de uma regra precisa ser um eixo existente.")
            valores = regra.get("valores")
            if not isinstance(valores, list) or not valores or any(
                not isinstance(v, str) or v not in dados["eixos"][alvo]["valores"] for v in valores
            ):
                raise ErroParametros("As correções de uma regra devem ser valores existentes do eixo alvo.")
    frases_ousadia = dados.get("frases_ousadia", {})
    if not isinstance(frases_ousadia, dict) or any(
        not isinstance(valor, str) or not isinstance(frase, str) for valor, frase in frases_ousadia.items()
    ):
        raise ErroParametros("frases_ousadia precisa mapear valores para frases de controle.")
    controles = {chave: dados.get(chave, padrao) for chave, padrao in PADROES_CONTROLES.items()}
    if not isinstance(controles["anti_repeticao"], bool):
        raise ErroParametros("anti_repeticao deve ser verdadeiro ou falso.")
    for chave, minimo, maximo in (("n_historico", 1, 200), ("abertura_palavras", 1, 30), ("similaridade_ngramas", 1, 10), ("max_tentativas", 1, 10)):
        valor = controles[chave]
        if not isinstance(valor, int) or isinstance(valor, bool) or not minimo <= valor <= maximo:
            raise ErroParametros(f"{chave} deve ser um inteiro entre {minimo} e {maximo}.")
    if not _numero(controles["limiar_similaridade"]) or not 0 <= controles["limiar_similaridade"] <= 1:
        raise ErroParametros("limiar_similaridade deve estar entre zero e um.")
    resultado = copy.deepcopy(dados)
    resultado.setdefault("versao", "1")
    resultado["temperatura"] = copy.deepcopy(temperatura)
    resultado.update(controles)
    return resultado


def carregar_ambiente(caminho=None) -> dict:
    """Leia .env e variáveis injetadas; o processo sempre tem precedência.

    O retorno pode conter segredos: use-o somente para configuração interna e
    nunca o inclua em respostas, exportações ou logs. Não altera ``os.environ``.
    """
    arquivo = Path(caminho or os.environ.get("GERADOR_ENV_FILE") or CAMINHO_ENV)
    locais = dotenv_values(arquivo, interpolate=False) if arquivo.is_file() else {}
    return {**{chave: valor for chave, valor in locais.items() if valor is not None}, **os.environ}


def carregar_configuracao(caminho=None, *, path=None) -> dict:
    """Releia o YAML a cada operação; mudanças externas dispensam editar Python.

    ``GERADOR_PARAMETERS_FILE`` permite apontar para um catálogo local alternativo.
    Nunca lê, devolve ou imprime valores de credenciais.
    """
    if caminho is not None and path is not None:
        raise ErroParametros("Informe somente um caminho de configuração.")
    ambiente = carregar_ambiente()
    arquivo = Path(caminho or path or ambiente.get("GERADOR_PARAMETERS_FILE") or CAMINHO_PADRAO)
    try:
        with arquivo.open(encoding="utf-8") as entrada:
            dados = yaml.safe_load(entrada)
    except (OSError, UnicodeError, yaml.YAMLError) as erro:
        raise ErroParametros("Não foi possível ler a configuração YAML do gerador.") from erro
    return _validar_configuracao(dados)


def catalogo_parametros(*, configuracao=None) -> dict:
    config = carregar_configuracao() if configuracao is None else _validar_configuracao(configuracao)
    return {
        "versao": str(config["versao"]),
        "parametros": [
            {
                "id": eixo,
                "rotulo": config.get("rotulos_eixos", {}).get(eixo, humanizar(eixo)),
                "camada": definicao.get("camada", 1),
                "opcoes": [
                    {"valor": valor, "rotulo": config.get("rotulos", {}).get(eixo, {}).get(valor, humanizar(valor))}
                    for valor in definicao["valores"]
                ],
            }
            for eixo, definicao in config["eixos"].items()
        ],
        "temperatura": copy.deepcopy(config["temperatura"]),
        "tamanho_frases": copy.deepcopy(config.get("tamanho_frases", {})),
        "padroes": {**{chave: config[chave] for chave in PADROES_CONTROLES}, "modo_sorteio": "ponderado"},
    }
