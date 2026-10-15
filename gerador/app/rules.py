"""Regras declarativas com correções que preservam escolhas fixas."""

from __future__ import annotations

import random

from .config import carregar_configuracao


def regra_violada(parametros: dict, regra: dict) -> bool:
    if not all(
        eixo in parametros and parametros[eixo] in (valor if isinstance(valor, list) else [valor])
        for eixo, valor in regra["quando"].items()
    ):
        return False
    alvo = regra.get("eixo")
    if alvo is not None:
        if alvo not in parametros:
            return False
        if parametros[alvo] in regra.get("valores", []):
            return False
    return True


def eixos_regra(regra):
    return set(regra["quando"]) | ({regra["eixo"]} if regra.get("eixo") else set())


def motivos_incompatibilidade(parametros: dict, *, configuracao=None) -> list[str]:
    config = carregar_configuracao() if configuracao is None else configuracao
    return [
        regra.get("mensagem") or "A combinação de parâmetros é incompatível."
        for regra in config.get("regras", []) if regra_violada(parametros, regra)
    ]


def compativel(parametros: dict, *, configuracao=None, fixos=None) -> bool:
    """Atribuições parciais são aceitas; conflitos inteiramente fixos são avisos.

    Sem ``fixos``, preserva a consulta lógica de compatibilidade. O amostrador
    informa os fixos para nunca proibir uma escolha explícita do usuário.
    """
    config = carregar_configuracao() if configuracao is None else configuracao
    return not any(
        regra_violada(parametros, regra)
        and (fixos is None or not eixos_regra(regra).issubset(fixos))
        for regra in config.get("regras", [])
    )


def _escolher(valores, eixo, config, rng, modo_sorteio):
    if modo_sorteio == "uniforme":
        return rng.choice(valores)
    pesos = config["eixos"][eixo].get("pesos", {})
    ponderacoes = [pesos.get(valor, 1) for valor in valores]
    return rng.choices(valores, weights=ponderacoes, k=1)[0] if any(ponderacoes) else rng.choice(valores)


def aplicar_regras(parametros, fixos=(), *, rng=None, modo_sorteio="ponderado", configuracao=None):
    """Corrija apenas eixos sorteados, registrando correções e conflitos restantes."""
    config = carregar_configuracao() if configuracao is None else configuracao
    rng = random.SystemRandom() if rng is None else rng
    resultado = dict(parametros)
    fixos = set(fixos)
    avisos = []
    ajustados = set()
    visitados = set()
    while True:
        assinatura = tuple(resultado.items())
        if assinatura in visitados:
            avisos.append("As regras de compatibilidade entraram em conflito; algumas combinações foram preservadas com aviso.")
            break
        visitados.add(assinatura)
        alterou = False
        for regra in config.get("regras", []):
            if not regra_violada(resultado, regra):
                continue
            mensagem = regra.get("mensagem") or "A combinação de parâmetros é incompatível."
            alvo = regra.get("eixo")
            valores = list(regra.get("valores", []))
            if alvo in fixos or alvo is None:
                alternativas = []
                for eixo, proibidos in regra["quando"].items():
                    if eixo in fixos:
                        continue
                    proibidos = proibidos if isinstance(proibidos, list) else [proibidos]
                    opcoes = [v for v in config["eixos"][eixo]["valores"] if v not in proibidos]
                    if opcoes:
                        alternativas.append((eixo, opcoes))
                if alternativas:
                    alvo, valores = rng.choice(alternativas)
                else:
                    avisos.append("Escolhas fixas preservadas: " + mensagem)
                    continue
            if not valores:
                avisos.append("Não foi possível corrigir a combinação: " + mensagem)
                continue
            novo = _escolher(valores, alvo, config, rng, modo_sorteio)
            if novo != resultado[alvo]:
                anterior = resultado[alvo]
                resultado[alvo] = novo
                ajustados.add(alvo)
                avisos.append(f"Compatibilidade: {alvo} ajustado de {anterior} para {novo}. {mensagem}")
                alterou = True
        if not alterou:
            break
    return {"parametros": resultado, "avisos": list(dict.fromkeys(avisos)), "ajustados": sorted(ajustados)}
