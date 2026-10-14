"""Zoom sintático aditivo de um período, sem alterar as etapas 04–09.

Constituintes vêm exclusivamente de um analisador de constituintes adicional.
Orações são propostas separadas por dependências: cada token pertence ao
predicado ancestral mais próximo. Assim, auxiliares e cópulas não criam novas
orações, alcances podem ser descontínuos e coordenação não vira subordinação.
O método não recupera predicados elípticos nem subtipos gramaticais tradicionais.
Ler/validar registros salvos nunca importa nem executa o modelo adicional.
"""

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import json
from uuid import uuid4

from preparacao import mapear_intervalos
from sintaxe_entidades import ErroAnalise, validar_analise_sintaxe_entidades


SCHEMA_VERSION = "1.0.0"
ETAPA = "hierarquia_sintatica"
MAX_TOKENS_PERIODO = 150
MAX_NOS = 1600
MAX_PROFUNDIDADE = 64
_METODO_ORACOES = "predicados_por_dependencias_v1"
_LIMITES = {"tokens_periodo": MAX_TOKENS_PERIODO, "nos": MAX_NOS, "profundidade": MAX_PROFUNDIDADE}
_CONSTITUINTES = {
    "S": "Oração", "SBAR": "Oração subordinada", "NP": "Sintagma nominal",
    "VP": "Sintagma verbal", "PP": "Sintagma preposicional",
    "ADJP": "Sintagma adjetival", "ADVP": "Sintagma adverbial",
}
_CLASSES = {
    "ADJ": "Adjetivo", "ADP": "Adposição", "ADV": "Advérbio", "AUX": "Verbo auxiliar",
    "CCONJ": "Conjunção coordenativa", "DET": "Determinante", "INTJ": "Interjeição",
    "NOUN": "Substantivo", "NUM": "Numeral", "PART": "Partícula", "PRON": "Pronome",
    "PROPN": "Nome próprio", "PUNCT": "Pontuação", "SCONJ": "Conjunção subordinativa",
    "SYM": "Símbolo", "VERB": "Verbo", "X": "Classe não determinada",
}
_FUNCOES = {
    "root": "Núcleo da estrutura sintática", "nsubj": "Núcleo do sujeito", "csubj": "Núcleo de oração com função de sujeito",
    "obj": "Núcleo do objeto", "iobj": "Núcleo do objeto indireto", "obl": "Núcleo de complemento ou modificador oblíquo",
    "amod": "Modificador adjetival", "advmod": "Modificador adverbial", "nmod": "Modificador nominal",
    "det": "Determinante", "nummod": "Modificador numeral", "appos": "Aposto",
    "aux": "Auxiliar", "cop": "Cópula", "case": "Marcador de caso",
    "cc": "Conjunção de coordenação", "mark": "Marcador de subordinação",
    "conj": "Elemento coordenado", "acl": "Oração modificadora de nome",
    "advcl": "Oração modificadora adverbial", "ccomp": "Complemento oracional",
    "xcomp": "Complemento com predicação aberta", "punct": "Pontuação",
    "parataxis": "Relação de parataxe", "flat": "Parte de expressão sem núcleo interno definido",
    "fixed": "Parte de expressão fixa", "compound": "Parte de expressão composta",
    "expl": "Elemento expletivo", "dep": "Relação sintática não especificada",
}
_AVISOS = [
    "Constituintes e dependências são previsões de modelos e podem divergir entre si.",
    "Orações são propostas por núcleos verbais ou predicados copulares; elipses e subtipos tradicionais não são determinados.",
    "A função sintática exibida traduz a dependência recebida; relações sem equivalência segura permanecem não determinadas.",
]


class ErroHierarquia(ValueError):
    """Entrada ou registro de hierarquia inconsistente."""


class ErroModelo(ErroHierarquia):
    """Modelo de constituintes indisponível ou incapaz de processar o período."""


class ErroAlinhamento(ErroHierarquia):
    """Árvore não corresponde exatamente aos tokens do período."""


class ErroLimite(ErroHierarquia):
    """Limite explícito excedido, sem truncamento."""


def _exigir(condicao, mensagem, erro=ErroHierarquia):
    if not condicao:
        raise erro(mensagem)


def _id(valor, nome):
    _exigir(isinstance(valor, str) and bool(valor.strip()), f"{nome} deve ser uma string não vazia.")
    return valor


def _igual(valor, esperado):
    # Igualdade Python confunde True com 1 e 1.0; coordenadas exigem tipos exatos.
    return json.dumps(valor, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(
        esperado, sort_keys=True, ensure_ascii=False, allow_nan=False)


@lru_cache(maxsize=1)
def _adaptador_padrao():
    from modelo_constituintes import ModeloConstituintes
    return ModeloConstituintes()


def _json(valor):
    """Exige JSON estrito, incluindo chaves textuais, sem imprimir conteúdo."""
    pilha, vistos = [valor], set()
    while pilha:
        item = pilha.pop()
        if isinstance(item, (dict, list)):
            if id(item) in vistos:
                continue
            vistos.add(id(item))
            if isinstance(item, dict):
                _exigir(all(isinstance(chave, str) for chave in item), "Chaves JSON devem ser textuais.")
                pilha.extend(item.values())
            else:
                pilha.extend(item)
        else:
            _exigir(item is None or type(item) in (str, int, float, bool), "Valor não pertence aos tipos JSON.")
    try:
        json.dumps(valor, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as erro:
        raise ErroHierarquia("Registro deve ser JSON UTF-8 estrito, sem ciclos.") from erro


def _data(valor, *, gerar=False):
    if valor is None and gerar:
        valor = datetime.now(timezone.utc).isoformat()
    elif isinstance(valor, datetime) and gerar:
        valor = valor.isoformat()
    _exigir(isinstance(valor, str), "registrado_em deve conter data ISO 8601 com fuso.")
    try:
        instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        _exigir(instante.tzinfo is not None and instante.utcoffset() is not None, "registrado_em deve conter fuso.")
    except (ValueError, OverflowError) as erro:
        raise ErroHierarquia("registrado_em deve conter data ISO 8601 válida com fuso.") from erro
    return valor


def _fonte(analise, periodo_id):
    try:
        validar_analise_sintaxe_entidades(analise)
    except (ErroAnalise, TypeError, KeyError, RecursionError) as erro:
        raise ErroHierarquia(f"Análise de origem inválida: {erro}") from erro
    _id(periodo_id, "periodo_id")
    segmentacao = analise["anotacao"]["segmentacao"]
    periodo = next((p for p in segmentacao["periodos"] if p["id"] == periodo_id), None)
    _exigir(periodo is not None, "Período não pertence à análise selecionada.")
    por_id = {t["id"]: t for t in segmentacao["tokens"]}
    tokens = [por_id[ident] for ident in periodo["tokens"]]
    _exigir(len(tokens) <= MAX_TOKENS_PERIODO,
            f"Este período excede o limite de {MAX_TOKENS_PERIODO} tokens; nenhum conteúdo foi truncado.", ErroLimite)
    return segmentacao, periodo, tokens


def _converter_arvore(bruta, tokens, preparacao, identificador):
    """Converte árvore pretokenizada; jamais recupera tokens por busca textual."""
    cursor, contador, vistos, nos = 0, 0, set(), []
    texto = preparacao["trabalho"]["texto"]

    def converter(valor, profundidade):
        nonlocal cursor, contador
        _exigir(profundidade <= MAX_PROFUNDIDADE, "Árvore excede o limite de profundidade.", ErroLimite)
        _exigir(isinstance(valor, dict), "Nós do analisador devem ser objetos.", ErroAlinhamento)
        _exigir(id(valor) not in vistos, "Árvore contém ciclo ou nó compartilhado.", ErroAlinhamento)
        vistos.add(id(valor))
        numero = contador
        contador += 1
        _exigir(contador <= MAX_NOS, "Árvore excede o limite de nós.", ErroLimite)
        inicio_token = cursor
        if "filhos" in valor:
            _exigir(set(valor) == {"rotulo", "filhos"}, "Formato de constituinte do analisador inválido.", ErroAlinhamento)
            rotulo = valor["rotulo"]
            _exigir(isinstance(rotulo, str) and bool(rotulo.strip()) and len(rotulo) <= 100,
                    "Rótulo nativo ausente ou inválido.", ErroAlinhamento)
            _exigir(isinstance(valor["filhos"], list) and bool(valor["filhos"]),
                    "Constituinte deve conter filhos.", ErroAlinhamento)
            filhos = [converter(filho, profundidade + 1) for filho in valor["filhos"]]
            item = {"tipo": "constituinte", "rotulo": rotulo,
                    "classificacao": _CONSTITUINTES.get(rotulo, "Constituinte de classificação não determinada"), "filhos": filhos}
        else:
            _exigir(set(valor) == {"texto"} and isinstance(valor["texto"], str),
                    "Formato de folha do analisador inválido.", ErroAlinhamento)
            _exigir(cursor < len(tokens) and valor["texto"] == tokens[cursor]["texto"],
                    "Folhas divergem dos tokens originais em texto, quantidade ou ordem.", ErroAlinhamento)
            item = {"tipo": "palavra", "rotulo": None, "classificacao": "Palavra ou sinal",
                    "token_id": tokens[cursor]["id"], "filhos": []}
            cursor += 1
        selecionados = tokens[inicio_token:cursor]
        _exigir(bool(selecionados), "Nó sem tokens correspondentes.", ErroAlinhamento)
        inicio, fim = selecionados[0]["trabalho"]["inicio"], selecionados[-1]["trabalho"]["fim"]
        item.update(id=f"{identificador}:no:{numero}", token_ids=[t["id"] for t in selecionados],
                    texto=texto[inicio:fim], trabalho={"inicio": inicio, "fim": fim})
        nos.append(item)
        return item

    arvore = converter(bruta, 0)
    _exigir(arvore["tipo"] == "constituinte", "Raiz deve ser um constituinte.", ErroAlinhamento)
    _exigir(cursor == len(tokens), "Árvore não cobre todos os tokens do período.", ErroAlinhamento)
    originais = mapear_intervalos(preparacao, [(n["trabalho"]["inicio"], n["trabalho"]["fim"]) for n in nos])
    for no, original in zip(nos, originais):
        no["original"] = original
    return arvore


def _bruta_salva(arvore):
    """Extrai somente a previsão, mantendo limites antes de qualquer recursão."""
    vistos, contador = set(), 0

    def extrair(no, profundidade):
        nonlocal contador
        _exigir(profundidade <= MAX_PROFUNDIDADE, "Árvore salva excede a profundidade.")
        _exigir(isinstance(no, dict) and id(no) not in vistos, "Árvore salva inválida, cíclica ou compartilhada.")
        vistos.add(id(no))
        contador += 1
        _exigir(contador <= MAX_NOS, "Árvore salva excede o limite de nós.")
        _exigir(no.get("tipo") in ("palavra", "constituinte"), "Tipo de nó salvo inválido.")
        _exigir(isinstance(no.get("filhos"), list), "Filhos do nó salvo devem formar uma lista.")
        if no["tipo"] == "palavra":
            _exigir(not no["filhos"], "Palavra salva não pode conter filhos.")
            return {"texto": no.get("texto")}
        return {"rotulo": no.get("rotulo"), "filhos": [extrair(f, profundidade + 1) for f in no["filhos"]]}

    return extrair(arvore, 0)


def _base(dependencia):
    return dependencia.lower().split(":", 1)[0]


def _palavras(analise, tokens):
    morfologia = {a["token_id"]: a for a in analise["anotacao"]["anotacoes_tokens"]}
    sintaxe = {a["token_id"]: a for a in analise["sintaxe"]}
    resultado = []
    for token in tokens:
        anotacao, relacao = morfologia[token["id"]], sintaxe[token["id"]]
        resultado.append({
            "token_id": token["id"], "texto": token["texto"], "lema": anotacao["lema"],
            "pos": anotacao["pos"], "classe": _CLASSES.get(anotacao["pos"], "Classe não determinada"),
            "morfologia": deepcopy(anotacao["morfologia"]), "dependencia": relacao["dependencia"],
            "funcao_sintatica": _FUNCOES.get(_base(relacao["dependencia"]), "Relação sintática não determinada"),
            "cabeca_token_id": relacao["cabeca_token_id"],
            "trabalho": deepcopy(token["trabalho"]), "original": deepcopy(token["original"]),
        })
    return resultado


def _papeis_constituintes(arvore, palavras):
    """Candidatos por dependências, independentes dos rótulos do constituinte.

Várias cabeças externas são preservadas quando os analisadores divergem.
Oblíquos e modificadores nominais não são forçados a complemento/modificador.
"""
    por_id = {p["token_id"]: p for p in palavras}
    pilha = [arvore]
    while pilha:
        no = pilha.pop()
        pilha.extend(no["filhos"])
        if no["tipo"] != "constituinte":
            continue
        ids = set(no["token_ids"])
        nucleos = [ident for ident in no["token_ids"]
                   if por_id[ident]["cabeca_token_id"] not in ids or por_id[ident]["cabeca_token_id"] == ident]
        no["origem_papeis"] = "candidatos_por_dependencias_v1"
        no["nucleo_token_ids"] = nucleos
        for chave, deps in (("complemento_token_ids", {"obj", "iobj", "ccomp", "xcomp"}),
                            ("modificador_token_ids", {"amod", "advmod", "nummod", "acl", "advcl", "appos"})):
            no[chave] = [ident for ident in no["token_ids"] if ident not in nucleos
                         and por_id[ident]["cabeca_token_id"] in nucleos
                         and _base(por_id[ident]["dependencia"]) in deps]


def _oracoes(palavras, preparacao, identificador):
    por_id = {p["token_id"]: p for p in palavras}
    ordem = {p["token_id"]: i for i, p in enumerate(palavras)}
    dep = {p["token_id"]: _base(p["dependencia"]) for p in palavras}
    nucleos = {p["token_id"] for p in palavras if p["pos"] in {"VERB", "AUX"}
               and dep[p["token_id"]] not in {"aux", "cop"}
               and p["texto"] not in {'"', "'", "“", "”", "«", "»", "‘", "’"}}
    nucleos.update(p["cabeca_token_id"] for p in palavras if dep[p["token_id"]] == "cop")
    ordenados = sorted(nucleos, key=ordem.__getitem__)
    ids = {n: f"{identificador}:oracao:{i}" for i, n in enumerate(ordenados)}
    pertence = {}

    def ancestral(ident, *, incluir=True):
        atual = ident if incluir else por_id[ident]["cabeca_token_id"]
        if not incluir and atual == ident:
            return None
        while True:
            if atual in nucleos:
                return atual
            seguinte = por_id[atual]["cabeca_token_id"]
            if seguinte == atual:
                return None
            atual = seguinte

    for palavra in palavras:
        pertence[palavra["token_id"]] = ancestral(palavra["token_id"])
    pais = {nucleo: ancestral(nucleo, incluir=False) for nucleo in ordenados}

    def pai_estrutural(nucleo):
        # Coordenadas compartilham o pai da oração coordenada, inclusive
        # dentro de uma subordinada. A aresta técnica conj fica em relacoes.
        atual = nucleo
        while pais[atual] is not None and dep[atual] == "conj":
            atual = pais[atual]
        pai = pais[atual]
        return ids[pai] if pai is not None and dep[atual] in {"xcomp", "ccomp", "advcl", "acl", "csubj"} else None

    resultado, trechos = [], []
    texto = preparacao["trabalho"]["texto"]
    for nucleo in ordenados:
        auxiliares = [p["token_id"] for p in palavras if p["cabeca_token_id"] == nucleo
                      and dep[p["token_id"]] in {"aux", "cop"}]
        grupo = sorted([nucleo] + auxiliares, key=ordem.__getitem__)
        alcance = [p["token_id"] for p in palavras if pertence[p["token_id"]] == nucleo]
        conectivos = {ident for ident in alcance if dep[ident] in {"cc", "mark"}}
        while True:
            adicionais = {ident for ident in alcance if dep[ident] == "fixed" and por_id[ident]["cabeca_token_id"] in conectivos}
            if adicionais <= conectivos:
                break
            conectivos.update(adicionais)
        grupos = []
        for ident in alcance:
            if grupos and ordem[ident] == ordem[grupos[-1][-1]] + 1:
                grupos[-1].append(ident)
            else:
                grupos.append([ident])
        fragmentos = []
        for grupo_ids in grupos:
            inicio, fim = por_id[grupo_ids[0]]["trabalho"]["inicio"], por_id[grupo_ids[-1]]["trabalho"]["fim"]
            trecho = {"token_ids": grupo_ids, "texto": texto[inicio:fim], "trabalho": {"inicio": inicio, "fim": fim}}
            trechos.append(trecho)
            fragmentos.append(trecho)
        pai = pais[nucleo]
        relacoes = []
        if pai is not None:
            tipo = "coordenacao" if dep[nucleo] == "conj" else "subordinacao" if dep[nucleo] in {"xcomp", "ccomp", "advcl", "acl", "csubj"} else "associacao_sintatica"
            relacoes.append({"tipo": tipo, "alvo_oracao_id": ids[pai], "dependencia": por_id[nucleo]["dependencia"]})
        copular = any(dep[a] == "cop" for a in auxiliares)
        resultado.append({"id": ids[nucleo], "tipo_predicado": "copular" if copular else "verbal",
                          "dependencia": por_id[nucleo]["dependencia"],
                          "nucleo_token_ids": [nucleo], "grupo_verbal_token_ids": grupo,
                          "conectivo_token_ids": sorted(conectivos, key=ordem.__getitem__),
                          "subtipo": "Não determinado pelo método",
                          "token_ids": alcance, "trechos": fragmentos, "relacoes": relacoes,
                          "pai_oracao_id": pai_estrutural(nucleo)})
    originais = mapear_intervalos(preparacao, [(t["trabalho"]["inicio"], t["trabalho"]["fim"]) for t in trechos])
    for trecho, original in zip(trechos, originais):
        trecho["original"] = original
    _classificar_relacoes_oracionais(resultado)
    return resultado


def _classificar_relacoes_oracionais(oracoes):
    """Nomes relacionais sustentados pelos vínculos, sem subtipos inferidos.

Uma subordinada pode reger outra oração e também participar de coordenação.
Ambas as pontas da coordenação são classificadas, apesar da representação
assimétrica do parser. Ausência de vínculo não prova uma oração absoluta.
"""
    principais = {o["pai_oracao_id"] for o in oracoes if o["pai_oracao_id"] is not None}
    coordenadas = set()
    for oracao in oracoes:
        for relacao in oracao["relacoes"]:
            if relacao["tipo"] == "coordenacao":
                coordenadas.update((oracao["id"], relacao["alvo_oracao_id"]))
    for oracao in oracoes:
        classes = []
        if oracao["id"] in principais:
            classes.append("principal")
        if oracao["pai_oracao_id"] is not None:
            classes.append("subordinada")
        if oracao["id"] in coordenadas:
            classes.append("coordenada")
        oracao["classificacoes_relacionais"] = classes
        if classes:
            nomes = classes[0] if len(classes) == 1 else ", ".join(classes[:-1]) + " e " + classes[-1]
            oracao["rotulo"] = "Oração " + nomes
        else:
            oracao["rotulo"] = "Oração sem relação oracional identificada"


def _relatorio(tokens, arvore, oracoes):
    pilha, quantidade = [arvore], 0
    while pilha:
        no = pilha.pop()
        quantidade += 1
        pilha.extend(no["filhos"])
    return {"estado": "hierarquia_validada", "pronto": True,
            "cobertura": {"tokens": len(tokens), "nos": quantidade, "oracoes": len(oracoes)},
            "verificacoes": ["origem_exata", "folhas_texto_ids_e_ordem", "arvore_limites_e_encaixe",
                              "intervalos_e_mapa_original", "oracoes_e_palavras_reproduziveis"]}


def construir_hierarquia(analise, periodo_id, *, adaptador=None, hierarquia_id=None, registrado_em=None):
    """Analisa um período exato; preserva a análise fornecida e sua tokenização."""
    segmentacao, periodo, tokens = _fonte(analise, periodo_id)
    identificador = str(uuid4()) if hierarquia_id is None else _id(hierarquia_id, "hierarquia_id")
    data = _data(registrado_em, gerar=True)
    if adaptador is None:
        try:
            adaptador = _adaptador_padrao()
        except (ImportError, ValueError, OSError) as erro:
            raise ErroModelo("Modelo de constituintes indisponível. Confira a instalação e os artefatos.") from erro
    try:
        previsao = adaptador.parse([t["texto"] for t in tokens])
        proveniencia = deepcopy(adaptador.provenance())
    except Exception as erro:
        # Erros nativos de alinhamento têm tradução própria para resposta HTTP 422.
        if erro.__class__.__name__ == "AlinhamentoConstituintesError":
            raise ErroAlinhamento(str(erro)) from erro
        if isinstance(erro, ErroHierarquia):
            raise
        raise ErroModelo(f"Não foi possível analisar os constituintes deste período: {erro}") from erro
    _exigir(isinstance(proveniencia, dict) and bool(proveniencia), "Proveniência do modelo ausente.", ErroModelo)
    _json(proveniencia)
    preparacao = segmentacao["preparacao"]
    arvore = _converter_arvore(previsao, tokens, preparacao, identificador)
    palavras = _palavras(analise, tokens)
    _papeis_constituintes(arvore, palavras)
    oracoes = _oracoes(palavras, preparacao, identificador)
    registro = {
        "schema_version": SCHEMA_VERSION, "etapa": ETAPA, "hierarquia_id": identificador,
        "registrado_em": data, **{c: analise[c] for c in ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id", "analise_id")},
        "periodo_id": periodo_id, "paragrafo_id": periodo["paragrafo_id"], "analise": deepcopy(analise),
        "coordenadas": deepcopy(analise["coordenadas"]),
        "processamento": {"constituencia": proveniencia, "oracoes": _METODO_ORACOES, "limites": deepcopy(_LIMITES)},
        "arvore": arvore, "oracoes": oracoes, "palavras": palavras, "avisos": list(_AVISOS),
    }
    if not oracoes:
        registro["avisos"].append("Nenhum núcleo oracional foi identificado; a árvore de constituintes e as palavras continuam disponíveis.")
    registro["validacao"] = _relatorio(tokens, arvore, oracoes)
    _json(registro)
    return registro


def validar_hierarquia(registro):
    """Valida origem, árvore e dados derivados sem carregar qualquer modelo."""
    _exigir(isinstance(registro, dict), "Hierarquia deve ser um objeto JSON.")
    _json(registro)
    obrigatorios = {"schema_version", "etapa", "hierarquia_id", "registrado_em", "documento_id", "preparacao_id",
                    "segmentacao_id", "anotacao_id", "analise_id", "periodo_id", "paragrafo_id", "analise",
                    "coordenadas", "processamento", "arvore", "oracoes", "palavras", "avisos", "validacao"}
    _exigir(set(registro) == obrigatorios, "Campos da hierarquia ausentes ou não suportados.")
    _exigir(registro["schema_version"] == SCHEMA_VERSION and registro["etapa"] == ETAPA, "Formato de hierarquia não suportado.")
    identificador = _id(registro["hierarquia_id"], "hierarquia_id")
    _data(registro["registrado_em"])
    segmentacao, periodo, tokens = _fonte(registro["analise"], registro["periodo_id"])
    for campo in ("documento_id", "preparacao_id", "segmentacao_id", "anotacao_id", "analise_id", "coordenadas"):
        esperado = registro["analise"][campo]
        _exigir(type(registro[campo]) is type(esperado) and registro[campo] == esperado, f"{campo} diverge da origem.")
    _exigir(registro["paragrafo_id"] == periodo["paragrafo_id"], "Parágrafo diverge do período de origem.")
    processamento = registro["processamento"]
    _exigir(isinstance(processamento, dict) and set(processamento) == {"constituencia", "oracoes", "limites"}, "Processamento inválido.")
    _exigir(isinstance(processamento["constituencia"], dict) and bool(processamento["constituencia"]), "Proveniência do modelo ausente.")
    _exigir(processamento["oracoes"] == _METODO_ORACOES and _igual(processamento["limites"], _LIMITES), "Método ou limites não suportados.")
    arvore = _converter_arvore(_bruta_salva(registro["arvore"]), tokens, segmentacao["preparacao"], identificador)
    palavras = _palavras(registro["analise"], tokens)
    _papeis_constituintes(arvore, palavras)
    _exigir(_igual(registro["arvore"], arvore), "IDs, classificação, intervalos ou cobertura da árvore divergem da origem.")
    _exigir(_igual(registro["palavras"], palavras), "Palavras e classificações divergem das anotações de origem.")
    oracoes = _oracoes(palavras, segmentacao["preparacao"], identificador)
    _exigir(_igual(registro["oracoes"], oracoes), "Orações, alcances ou relações divergem das dependências de origem.")
    avisos = list(_AVISOS)
    if not oracoes:
        avisos.append("Nenhum núcleo oracional foi identificado; a árvore de constituintes e as palavras continuam disponíveis.")
    _exigir(registro["avisos"] == avisos, "Limitações do método ausentes ou divergentes.")
    relatorio = _relatorio(tokens, arvore, oracoes)
    _exigir(_igual(registro["validacao"], relatorio), "Relatório de validação divergente.")
    _json(registro)
    return relatorio
