"""Amostragem ponderada e planejamento de lotes, sem materializar o produto."""

from __future__ import annotations

import math
import random
from contextvars import ContextVar
from functools import wraps

from .config import ErroParametros, _validar_configuracao, carregar_configuracao
from .rules import aplicar_regras, compativel


class _LimiteBusca(Exception):
    pass


_orcamento_busca = ContextVar("orcamento_sorteio", default=None)


def _limitar_busca(funcao):
    @wraps(funcao)
    def executar(*args, **kwargs):
        orcamento = {"restante": 12000 if kwargs.get("aplicar_compatibilidade", True) else 100000}
        token = _orcamento_busca.set(orcamento)
        try:
            return funcao(*args, **kwargs)
        except _LimiteBusca:
            return None
        finally:
            _orcamento_busca.reset(token)
    return executar


def validar_parametros(parametros, quantidade, *, configuracao=None, concretos=False):
    config = carregar_configuracao() if configuracao is None else configuracao
    if not isinstance(quantidade, int) or isinstance(quantidade, bool) or not 1 <= quantidade <= 20:
        raise ErroParametros("A quantidade deve ser um inteiro entre 1 e 20.")
    if not isinstance(parametros, dict):
        raise ErroParametros("Os parâmetros devem ser um objeto com os eixos escolhidos.")
    if any(not isinstance(eixo, str) or eixo not in config["eixos"] for eixo in parametros):
        raise ErroParametros("Foi informado um eixo de parâmetros desconhecido.")
    if concretos and set(parametros) != set(config["eixos"]):
        raise ErroParametros("O prompt precisa de um valor concreto para cada eixo.")
    resultado = {}
    for eixo, definicao in config["eixos"].items():
        valor = parametros.get(eixo, "aleatorio")
        especiais = () if concretos else ("aleatorio", "variar")
        if not isinstance(valor, str) or valor not in (*definicao["valores"], *especiais):
            raise ErroParametros(f"O valor informado para {eixo} é inválido.")
        if valor == "variar" and quantidade == 1:
            raise ErroParametros("O modo variar está disponível somente para geração em lote.")
        resultado[eixo] = valor
    return resultado


def _ordem_ponderada(opcoes, pesos, rng):
    # Uma permutação ponderada fornece uma primeira escolha proporcional ao peso;
    # o restante só é consultado se uma regra ou colisão impedir essa escolha.
    return sorted(
        opcoes,
        key=lambda valor: math.log(max(rng.random(), 1e-300)) / pesos.get(valor, 1),
        reverse=True,
    )


def _candidatos(pools, modos, config, rng=None, *, fixos=(), modo_sorteio="ponderado", aplicar_compatibilidade=True):
    eixos = list(config["eixos"])
    ordem = sorted(eixos, key=lambda eixo: len(pools[eixo]))
    atuais = {}

    def visitar(indice):
        orcamento = _orcamento_busca.get()
        if orcamento is not None:
            orcamento["restante"] -= 1
            if orcamento["restante"] < 0:
                raise _LimiteBusca
        if indice == len(ordem):
            yield {eixo: atuais[eixo] for eixo in eixos}
            return
        eixo = ordem[indice]
        valores = list(pools[eixo])
        if rng is not None:
            if modos[eixo] == "aleatorio" and modo_sorteio == "ponderado":
                valores = _ordem_ponderada(valores, config["eixos"][eixo].get("pesos", {}), rng)
            else:
                rng.shuffle(valores)
        for valor in valores:
            atuais[eixo] = valor
            if not aplicar_compatibilidade or compativel(atuais, configuracao=config, fixos=set(fixos)):
                yield from visitar(indice + 1)
        atuais.pop(eixo, None)

    yield from visitar(0)


@_limitar_busca
def _construir_plano(pools, modos, quantidade, config, rng, modo_sorteio, *, aplicar_compatibilidade=True):
    fixos = {eixo for eixo, modo in modos.items() if modo not in ("aleatorio", "variar")}

    def candidatos(disponiveis, sorteio=None):
        return _candidatos(disponiveis, modos, config, sorteio, fixos=fixos,
                           modo_sorteio=modo_sorteio, aplicar_compatibilidade=aplicar_compatibilidade)

    if aplicar_compatibilidade:
        # Detecte valores impossíveis antes de buscar milhões de alternativas
        # em outros eixos: um ciclo completo exige todos os seus valores.
        for eixo, opcoes in pools.items():
            if modos[eixo] != "variar":
                continue
            viaveis = 0
            for valor in opcoes:
                teste = dict(pools)
                teste[eixo] = [valor]
                if next(candidatos(teste), None) is not None:
                    viaveis += 1
            if viaveis < min(quantidade, len(opcoes)):
                return None
    # Só precisamos saber se há combinações suficientes para este lote.
    capacidade = 0
    for _ in candidatos(pools):
        capacidade += 1
        if capacidade >= quantidade:
            break
    if not capacidade:
        return None
    exigir_unicos = capacidade >= quantidade
    plano = []
    usados = set()
    eixos = list(config["eixos"])

    def completar():
        indice = len(plano)
        if indice == quantidade:
            return True
        disponiveis = {}
        for eixo, opcoes in pools.items():
            if modos[eixo] != "variar":
                disponiveis[eixo] = opcoes
                continue
            inicio_ciclo = indice - indice % len(opcoes)
            vistos_ciclo = {linha[eixo] for linha in plano[inicio_ciclo:]}
            disponiveis[eixo] = [v for v in opcoes if v not in vistos_ciclo]
        for candidato in candidatos(disponiveis, rng):
            assinatura = tuple(candidato[eixo] for eixo in eixos)
            if exigir_unicos and assinatura in usados:
                continue
            plano.append(candidato)
            usados.add(assinatura)
            if completar():
                return True
            plano.pop()
            if exigir_unicos or not any(tuple(p[e] for e in eixos) == assinatura for p in plano):
                usados.discard(assinatura)
        return False

    if not completar():
        return None
    return plano


def _componentes_regras(eixos, regras):
    componentes = [{eixo} for eixo in eixos]
    for regra in regras:
        envolvidos = set(regra["quando"]) | ({regra["eixo"]} if regra.get("eixo") else set())
        tocados = [grupo for grupo in componentes if grupo & envolvidos]
        if tocados:
            componentes = [grupo for grupo in componentes if not grupo & envolvidos]
            componentes.append(set().union(*tocados))
    return componentes


def _planejar_compativel(pools, modos, quantidade, config, rng, modo_sorteio):
    # Resolva componentes independentes separadamente. Sem isso uma restrição
    # impossível ao fim do lote provocaria retrocesso por bilhões de escolhas
    # equivalentes em eixos que nem participam daquela restrição.
    grupos = _componentes_regras(config["eixos"], config.get("regras", []))
    if len(grupos) > 1 and config.get("regras"):
        parciais = []
        for grupo in grupos:
            subconfig = dict(config)
            subconfig["eixos"] = {eixo: definicao for eixo, definicao in config["eixos"].items() if eixo in grupo}
            subconfig["regras"] = [regra for regra in config.get("regras", []) if set(regra["quando"]).issubset(grupo)]
            parcial = _construir_plano(
                {eixo: opcoes for eixo, opcoes in pools.items() if eixo in grupo},
                {eixo: modo for eixo, modo in modos.items() if eixo in grupo},
                quantidade, subconfig, rng, modo_sorteio,
            )
            if parcial is None:
                return None
            parciais.append(parcial)
        combinados = [
            {eixo: next(parcial[indice][eixo] for parcial in parciais if eixo in parcial[indice]) for eixo in config["eixos"]}
            for indice in range(quantidade)
        ]
        if len({tuple(linha.items()) for linha in combinados}) == quantidade:
            return combinados
        # Colisões entre componentes pequenos ainda exigem o planejamento
        # conjunto, para desencontrar os ciclos sem mudar a distribuição.
    return _construir_plano(pools, modos, quantidade, config, rng, modo_sorteio)


def _resolver(parametros, quantidade, rng, modo_sorteio, configuracao):
    config = carregar_configuracao() if configuracao is None else _validar_configuracao(configuracao)
    modos = validar_parametros(parametros, quantidade, configuracao=config)
    if not isinstance(modo_sorteio, str) or modo_sorteio not in ("ponderado", "uniforme"):
        raise ErroParametros("O modo de sorteio deve ser ponderado ou uniforme.")
    pools = {}
    for eixo, modo in modos.items():
        definicao = config["eixos"][eixo]
        if modo == "aleatorio":
            pools[eixo] = [v for v in definicao["valores"] if modo_sorteio == "uniforme" or definicao.get("pesos", {}).get(v, 1) > 0]
        else:
            pools[eixo] = list(definicao["valores"]) if modo == "variar" else [modo]
    plano = _planejar_compativel(pools, modos, quantidade, config, rng, modo_sorteio)
    flexibilizou = plano is None
    if plano is None:
        plano = _construir_plano(pools, modos, quantidade, config, rng, modo_sorteio, aplicar_compatibilidade=False)
    if plano is None:
        raise ErroParametros("Não foi possível planejar este lote dentro do limite de busca. Reduza a quantidade ou ajuste os parâmetros.")
    fixos = {eixo for eixo, modo in modos.items() if modo not in ("aleatorio", "variar")}
    finais = []
    avisos_por_item = []
    for linha in plano:
        correcao = aplicar_regras(linha, fixos, rng=rng, modo_sorteio=modo_sorteio, configuracao=config)
        finais.append(correcao["parametros"])
        avisos = correcao["avisos"]
        variados_ajustados = [eixo for eixo in correcao["ajustados"] if modos[eixo] == "variar"]
        if variados_ajustados:
            avisos.append("A compatibilidade exigiu ajustar o ciclo balanceado de variar: " + ", ".join(variados_ajustados) + ".")
        if flexibilizou and variados_ajustados:
            avisos.append("A distribuição originalmente balanceada foi flexibilizada para respeitar as regras e as escolhas fixas.")
        avisos_por_item.append(list(dict.fromkeys(avisos)))
    vistos = set()
    for indice, linha in enumerate(finais):
        assinatura = tuple(linha.items())
        if assinatura in vistos and flexibilizou:
            avisos_por_item[indice].append("Os ajustes de compatibilidade repetiram uma combinação de parâmetros neste lote.")
        vistos.add(assinatura)
    return {"planos": finais, "avisos_por_item": avisos_por_item}


def resolver_plano(parametros, quantidade, seed=None, modo_sorteio="ponderado", *, configuracao=None):
    """Planeje parâmetros e avisos; uma semente inteira reproduz a operação.

    Fixos são preservados. Tenta primeiro compatibilidade e ciclos completos;
    quando ambos forem inviáveis, aplica apenas as correções declaradas no YAML
    aos eixos sorteados e informa os ciclos afetados. O produto cartesiano é
    visitado preguiçosamente; não é materializado, mesmo com muitos eixos.
    """
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        raise ErroParametros("A semente deve ser um número inteiro.")
    rng = random.SystemRandom() if seed is None else random.Random(seed)
    return _resolver(parametros, quantidade, rng, modo_sorteio, configuracao)


def planejar_parametros(parametros, quantidade, rng=None, *, configuracao=None, modo_sorteio="ponderado") -> list[dict]:
    """Compatibilidade com o contrato anterior, retornando somente os planos.

    Use ``resolver_plano`` para também obter avisos. ``aleatorio`` usa reposição;
    ``variar`` usa ciclos sem reposição e ignora os pesos. A busca evita colisões
    quando a combinatória permite; correções podem reduzir essa diversidade e
    são registradas nos avisos. Não há garantia de diversidade semântica.
    """
    rng = random.SystemRandom() if rng is None else rng
    return _resolver(parametros, quantidade, rng, modo_sorteio, configuracao)["planos"]
