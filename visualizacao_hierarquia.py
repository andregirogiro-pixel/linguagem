"""Projeção de leitura de uma hierarquia salva, sem corrigir sua previsão.

A floresta de cada oração contém apenas nós completos da árvore original.
Um sintagma que inclui outra oração nunca é recortado para parecer menor:
seus descendentes completos são apresentados com suas referências originais.
Os dados v1 e a árvore integral continuam disponíveis para auditoria.
"""

from copy import deepcopy
import unicodedata

from hierarquia_sintatica import validar_hierarquia
from preparacao import mapear_intervalos


VERSAO_VISUALIZACAO = "1.0"
_CLASSIFICACOES = {
    "NP": "Sintagma nominal", "VP": "Sintagma verbal",
    "PP": "Sintagma preposicional", "ADJP": "Sintagma adjetival",
    "ADVP": "Sintagma adverbial", "QP": "Sintagma quantificador",
    "S": "Estrutura oracional", "SBAR": "Estrutura de subordinação",
    "CP": "Sintagma complementizador", "CONJP": "Sintagma conjuntivo",
    "PRN": "Estrutura parentética", "INTJP": "Sintagma interjetivo",
    "FRAG": "Fragmento na árvore do modelo", "UCP": "Estrutura de coordenação",
}
_CONTINERES_ORACIONAIS = {"S", "SBAR", "CP"}
_POS_UD = {"ADJ", "ADP", "ADV", "AUX", "CCONJ", "DET", "INTJ", "NOUN", "NUM",
           "PART", "PRON", "PROPN", "PUNCT", "SCONJ", "SYM", "VERB", "X"}
# Etiquetas lexicais usuais: somente preterminais de uma folha são ocultados.
_POS_NATIVOS = _POS_UD | {
    "N", "V", "A", "D", "P", "C", "R", "F", "PUNC", "PU", "PONCT",
    "NN", "NNS", "NNP", "NNPS", "VB", "VBD", "VBG", "VBN", "VBP", "VBZ",
    "JJ", "JJR", "JJS", "RB", "RBR", "RBS", "DT", "PDT", "WDT", "PRP",
    "PRP$", "WP", "WP$", "WRB", "IN", "TO", "CC", "CD", "EX", "FW",
    "LS", "MD", "POS", "RP", "UH", ".", ",", ":", "``", "''", "-LRB-", "-RRB-",
}
_AVISO_ENCAIXE = (
    "Há constituintes que abrangem outras orações. A visualização mostra somente "
    "subárvores completas desta oração; os encaixes permanecem na árvore integral do período."
)


def _pontuacao(texto):
    """Não ocultar palavras só porque o modelo lhes atribuiu POS=PUNCT."""
    return bool(texto) and all(unicodedata.category(letra).startswith("P") for letra in texto)


def _referencia(no):
    return {"id": no["id"], "rotulo": no["rotulo"]}


def _registrar_wrapper(raizes, no):
    for raiz in raizes:
        raiz["rotulos_tecnicos"] = [_referencia(no), *raiz.get("rotulos_tecnicos", [])]
    return raizes


def _preterminal(no):
    return (no["tipo"] == "constituinte" and no["rotulo"] in _POS_NATIVOS
            and len(no["filhos"]) == 1 and no["filhos"][0]["tipo"] == "palavra")


def _simplificar(no, ids_lexicos_oracao, ids_pontuacao):
    """Simplificar apenas invólucros, mantendo cada nó escolhido por inteiro."""
    item = deepcopy(no)
    item["rotulos_tecnicos"] = []
    if no["tipo"] == "palavra":
        return [item]
    filhos = []
    for filho in no["filhos"]:
        filhos.extend(_simplificar(filho, ids_lexicos_oracao, ids_pontuacao))
    item["filhos"] = filhos
    item["classificacao"] = _CLASSIFICACOES.get(no["rotulo"], "Estrutura sintática do modelo")
    ids_lexicos_no = set(no["token_ids"]) - ids_pontuacao
    recipiente_oracao = (no["rotulo"] in _CONTINERES_ORACIONAIS
                         and ids_lexicos_no == ids_lexicos_oracao)
    redundante = (len(no["filhos"]) == 1 and no["filhos"][0]["tipo"] == "constituinte"
                  and no["rotulo"] == no["filhos"][0]["rotulo"]
                  and no["token_ids"] == no["filhos"][0]["token_ids"])
    if no["rotulo"] == "ROOT" or _preterminal(no) or recipiente_oracao or redundante:
        return _registrar_wrapper(filhos, no)
    return [item]


def _floresta(arvore, ids_oracao, ids_pontuacao):
    lexicos = ids_oracao - ids_pontuacao
    houve_encaixe = False

    def visitar(no):
        nonlocal houve_encaixe
        ids_no = set(no["token_ids"])
        if not (ids_no & lexicos):
            return []
        if ids_no <= ids_oracao:
            return _simplificar(no, lexicos, ids_pontuacao)
        if no["tipo"] == "constituinte" and no["rotulo"] in _CLASSIFICACOES:
            houve_encaixe = True
        raizes = []
        for filho in no["filhos"]:
            raizes.extend(visitar(filho))
        return raizes

    raizes = visitar(arvore)
    # Pontuação separada do trecho não deve aparecer como um falso sintagma.
    raizes = [no for no in raizes if set(no["token_ids"]) - ids_pontuacao]
    return raizes, houve_encaixe


def _trechos_leitura(oracao, palavras, por_id, preparacao):
    """Retirar margens de pontuação sem juntar alcances descontínuos."""
    ordem = {palavra["token_id"]: i for i, palavra in enumerate(palavras)}
    grupos = []
    for ident in oracao["token_ids"]:
        if grupos and ordem[ident] == ordem[grupos[-1][-1]] + 1:
            grupos[-1].append(ident)
        else:
            grupos.append([ident])
    resultado = []
    texto = preparacao["trabalho"]["texto"]
    for grupo in grupos:
        while grupo and _pontuacao(por_id[grupo[0]]["texto"]):
            grupo.pop(0)
        while grupo and _pontuacao(por_id[grupo[-1]]["texto"]):
            grupo.pop()
        if not grupo:
            continue
        inicio = por_id[grupo[0]]["trabalho"]["inicio"]
        fim = por_id[grupo[-1]]["trabalho"]["fim"]
        resultado.append({"token_ids": grupo, "texto": texto[inicio:fim],
                          "trabalho": {"inicio": inicio, "fim": fim}})
    originais = mapear_intervalos(preparacao, [(t["trabalho"]["inicio"], t["trabalho"]["fim"]) for t in resultado])
    for trecho, original in zip(resultado, originais):
        trecho["original"] = original
    return resultado


def _rotulos_lexicais(arvore, palavras):
    por_id = {palavra["token_id"]: palavra for palavra in palavras}
    for palavra in palavras:
        palavra["rotulos_pos_modelo"] = []
        palavra["avisos"] = []
    pilha = [arvore]
    while pilha:
        no = pilha.pop()
        if _preterminal(no):
            palavra = por_id[no["filhos"][0]["token_id"]]
            rotulo = no["rotulo"]
            if rotulo not in palavra["rotulos_pos_modelo"]:
                palavra["rotulos_pos_modelo"].append(rotulo)
            if rotulo in _POS_UD and rotulo != palavra["pos"]:
                palavra["avisos"].append(
                    f"Os modelos divergem na classe gramatical: dependências={palavra['pos']}; constituintes={rotulo}."
                )
        pilha.extend(reversed(no["filhos"]))


def preparar_visualizacao(hierarquia):
    """Preparar dados de apresentação determinísticos de um registro v1 válido.

    Não carrega modelos, não altera o registro, nem chama a projeção de uma
    oração de árvore de constituintes completa. IDs e intervalos dos nós são
    referências à previsão salva; a árvore técnica é preservada uma única vez.
    """
    validar_hierarquia(hierarquia)
    palavras = deepcopy(hierarquia["palavras"])
    por_id = {palavra["token_id"]: palavra for palavra in palavras}
    pontuacao = {palavra["token_id"] for palavra in palavras if _pontuacao(palavra["texto"])}
    preparacao = hierarquia["analise"]["anotacao"]["segmentacao"]["preparacao"]
    _rotulos_lexicais(hierarquia["arvore"], palavras)
    oracoes = []
    for numero, original in enumerate(hierarquia["oracoes"], 1):
        oracao = deepcopy(original)
        oracao["numero"] = numero
        oracao["trechos_leitura"] = _trechos_leitura(oracao, palavras, por_id, preparacao)
        oracao["token_ids_leitura"] = [ident for trecho in oracao["trechos_leitura"] for ident in trecho["token_ids"]]
        oracao["raizes"], encaixe = _floresta(hierarquia["arvore"], set(oracao["token_ids"]), pontuacao)
        oracao["avisos"] = [_AVISO_ENCAIXE] if encaixe else []
        oracoes.append(oracao)
    raizes, _ = _floresta(hierarquia["arvore"], set(por_id), pontuacao)
    return {"versao": VERSAO_VISUALIZACAO, "hierarquia_id": hierarquia["hierarquia_id"],
            "periodo_id": hierarquia["periodo_id"], "oracoes": oracoes, "palavras": palavras,
            "raizes": raizes if not oracoes else [], "avisos": list(hierarquia["avisos"]),
            "arvore_tecnica": deepcopy(hierarquia["arvore"])}
