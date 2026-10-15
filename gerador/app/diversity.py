"""Filtros lexicais reproduzíveis; não avaliam equivalência semântica."""

from __future__ import annotations

import re
import math
import statistics
import unicodedata
from collections import Counter

from .config import ErroParametros, _validar_configuracao, carregar_configuracao


_IGNORADAS = set("""
a ao aos aquela aquelas aquele aqueles aqui as ate cada com como da das de dela
delas dele deles depois do dos e ela elas ele eles em entre era eram essa essas
esse esses esta estao estar estas estava estavam este estes eu foi foram ha isso
isto ja la lhe lhes mais mas me mesmo meu meus minha minhas muito na nao nas nem
no nos nossa nossas nosso nossos num numa o os ou para pela pelas pelo pelos por
porque quando que quem se sem ser seu seus sua suas tambem te tem tendo tenho
ter tinha tinham toda todas todo todos tu um uma umas uns voce voces vos era
""".split())


def palavras_normalizadas(texto: str) -> list[str]:
    decomposto = unicodedata.normalize("NFKD", texto).lower()
    sem_acentos = "".join(letra for letra in decomposto if not unicodedata.combining(letra))
    return re.findall(r"[^\W_]+", sem_acentos, flags=re.UNICODE)


def palavras_conteudo(texto: str) -> list[str]:
    return [p for p in palavras_normalizadas(texto) if len(p) > 2 and not p.isdecimal() and p not in _IGNORADAS]


def validar_anteriores(anteriores):
    if not isinstance(anteriores, list) or any(not isinstance(texto, str) for texto in anteriores):
        raise ErroParametros("Os parágrafos anteriores devem ser uma lista de textos.")


def avaliar_diversidade(texto, anteriores, *, anti_repeticao=True, n_historico=None, abertura_palavras=None, similaridade_ngramas=None, limiar_similaridade=None, configuracao=None) -> dict:
    """Compare aberturas e conjuntos de n-gramas; o limiar é estrito (>).

    Os n-gramas usam palavras normalizadas, incluindo palavras funcionais, para
    capturar sequências repetidas. O filtro é lexical, sem garantia semântica.
    """
    if not isinstance(texto, str) or not texto.strip():
        raise ErroParametros("A avaliação de diversidade precisa de um texto não vazio.")
    validar_anteriores(anteriores)
    config = carregar_configuracao() if configuracao is None else _validar_configuracao(configuracao)
    if not isinstance(anti_repeticao, bool):
        raise ErroParametros("anti_repeticao deve ser verdadeiro ou falso.")
    controles = {
        "n_historico": config["n_historico"] if n_historico is None else n_historico,
        "abertura_palavras": config["abertura_palavras"] if abertura_palavras is None else abertura_palavras,
        "similaridade_ngramas": config["similaridade_ngramas"] if similaridade_ngramas is None else similaridade_ngramas,
    }
    for chave, valor in controles.items():
        if not isinstance(valor, int) or isinstance(valor, bool) or valor < 1 or (chave == "n_historico" and valor > 200):
            raise ErroParametros(f"O controle {chave} deve ser um inteiro positivo" + (" até 200." if chave == "n_historico" else "."))
    limite = config["limiar_similaridade"] if limiar_similaridade is None else limiar_similaridade
    if not isinstance(limite, (int, float)) or isinstance(limite, bool) or not math.isfinite(limite) or not 0 <= limite <= 1:
        raise ErroParametros("O limiar de similaridade deve estar entre zero e um.")
    historico = anteriores[-controles["n_historico"]:] if anti_repeticao else []
    palavras = palavras_normalizadas(texto)
    abertura = palavras[:controles["abertura_palavras"]]
    tokens = palavras_conteudo(texto)
    tamanho_ngramas = controles["similaridade_ngramas"]

    def ngramas(itens):
        return {tuple(itens[i:i + tamanho_ngramas]) for i in range(len(itens) - tamanho_ngramas + 1)}

    conjunto = ngramas(palavras)
    motivos = set()
    maior_jaccard = 0.0
    for anterior in historico:
        palavras_anteriores = palavras_normalizadas(anterior)
        if palavras and palavras == palavras_anteriores:
            motivos.add("texto_repetido")
        if len(abertura) == controles["abertura_palavras"] and abertura == palavras_anteriores[:controles["abertura_palavras"]]:
            motivos.add("abertura_repetida")
        outro_conjunto = ngramas(palavras_anteriores)
        uniao = conjunto | outro_conjunto
        similaridade = len(conjunto & outro_conjunto) / len(uniao) if uniao else 0.0
        maior_jaccard = max(maior_jaccard, similaridade)
        if conjunto and outro_conjunto and similaridade > limite:
            motivos.add("vocabulario_semelhante")
    ordem = ("texto_repetido", "abertura_repetida", "vocabulario_semelhante")
    return {
        "aceito": not motivos,
        "motivos": [motivo for motivo in ordem if motivo in motivos],
        "metricas": {
            "paragrafos_comparados": len(historico),
            "maior_jaccard": round(maior_jaccard, 6),
            "abertura": " ".join(abertura),
            "palavras_conteudo": len(tokens),
            "similaridade_ngramas": tamanho_ngramas,
            "limiar_similaridade": limite,
            "anti_repeticao": anti_repeticao,
        },
    }


def calcular_metricas(textos, planos, *, configuracao=None):
    """Métricas agregadas; comprimento em palavras e desvio populacional."""
    validar_anteriores(textos)
    if not isinstance(planos, list) or len(planos) != len(textos) or any(
        not isinstance(plano, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in plano.items())
        for plano in planos
    ):
        raise ErroParametros("As métricas precisam de um plano de parâmetros para cada texto.")
    config = carregar_configuracao() if configuracao is None else _validar_configuracao(configuracao)
    listas = [palavras_normalizadas(texto) for texto in textos]
    todas = [palavra for lista in listas for palavra in lista]
    comprimentos = [len(lista) for lista in listas]
    aberturas = {tuple(lista[:config["abertura_palavras"]]) for lista in listas}
    distribuicao = {}
    for plano in planos:
        for eixo, valor in plano.items():
            distribuicao.setdefault(eixo, Counter())[valor] += 1
    return {
        "proporcao_aberturas_unicas": len(aberturas) / len(textos) if textos else 0.0,
        "diversidade_lexical": len(set(todas)) / len(todas) if todas else 0.0,
        "comprimento": {
            "media": statistics.mean(comprimentos) if comprimentos else 0.0,
            "desvio": statistics.pstdev(comprimentos) if comprimentos else 0.0,
        },
        "distribuicao_parametros": {eixo: dict(contagem) for eixo, contagem in distribuicao.items()},
    }
