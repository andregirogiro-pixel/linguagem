"""Painéis de leitura da segmentação sem alterar as análises armazenadas.

Os sintagmas exibidos são nós completos da árvore de constituintes. Relações
de dependência sugerem funções, sem transformar núcleos lexicais em sintagmas
fabricados. Morfologia e sintaxe compartilham a leitura revisada, mas ficam em
listas independentes para a interface.
"""

from copy import deepcopy
import re
import unicodedata

from analise_gramatical import primeira_pessoa_perfeito_verificada


_FRASES = {"NP": "Sintagma nominal", "PP": "Sintagma preposicional",
           "ADVP": "Sintagma adverbial", "ADJP": "Sintagma adjetival"}
_FUNCOES = {"nsubj", "csubj", "obj", "iobj", "obl", "advmod", "nmod", "amod", "appos"}
_VALORES = {
    "Person": {"1": "primeira pessoa", "2": "segunda pessoa", "3": "terceira pessoa"},
    "Number": {"Sing": "singular", "Plur": "plural"},
    "Tense": {"Pres": "presente", "Past": "pretérito perfeito", "Imp": "imperfeito",
              "Pqp": "mais-que-perfeito", "Fut": "futuro"},
    "Mood": {"Ind": "indicativo", "Sub": "subjuntivo", "Imp": "imperativo", "Cnd": "condicional"},
    "VerbForm": {"Fin": "forma finita", "Inf": "infinitivo", "Ger": "gerúndio", "Part": "particípio"},
    "Gender": {"Masc": "masculino", "Fem": "feminino", "Com": "gênero comum"},
    "Definite": {"Def": "definido", "Ind": "indefinido"},
    "PronType": {"Art": "artigo", "Dem": "demonstrativo", "Ind": "indefinido",
                 "Int": "interrogativo", "Prs": "pessoal", "Rel": "relativo", "Tot": "totalizante", "Neg": "negativo"},
    "Voice": {"Act": "voz ativa", "Pass": "voz passiva"},
    "Degree": {"Pos": "grau positivo", "Cmp": "comparativo", "Sup": "superlativo"},
    "Case": {"Nom": "nominativo", "Acc": "acusativo", "Dat": "dativo", "Gen": "genitivo"},
}


def _leitura(palavra):
    return palavra.get("revisao") or palavra


def _base(dependencia):
    return (dependencia or "").lower().split(":", 1)[0]


def _lexical(texto):
    return any(unicodedata.category(letra)[0] in {"L", "N"} for letra in texto)


def _nos(raizes):
    for raiz in raizes:
        yield raiz
        yield from _nos(raiz.get("filhos", []))


def _texto_tokens(ids, palavras, texto):
    """Preservar trechos literais; não inventar adjacência entre palavras."""
    grupos = []
    for ident in ids:
        palavra = palavras[ident]
        if grupos and texto[grupos[-1][1]:palavra["trabalho"]["inicio"]].isspace():
            grupos[-1][1] = palavra["trabalho"]["fim"]
        elif grupos and grupos[-1][1] == palavra["trabalho"]["inicio"]:
            grupos[-1][1] = palavra["trabalho"]["fim"]
        else:
            grupos.append([palavra["trabalho"]["inicio"], palavra["trabalho"]["fim"]])
    return " … ".join(texto[inicio:fim] for inicio, fim in grupos)


def _funcao(palavra, no, palavras):
    leitura = _leitura(palavra)
    dependencia = _base(palavra.get("dependencia"))
    if no and no.get("rotulo") == "PP":
        cabeca = palavras.get(palavra.get("cabeca_token_id"), {})
        if _leitura(cabeca).get("pos") in {"VERB", "AUX"}:
            return ("Expressão preposicionada ligada ao verbo",
                    "A distinção entre complemento e adjunto depende da construção e da abordagem gramatical.")
        if _leitura(cabeca).get("pos") in {"NOUN", "PROPN", "PRON"}:
            return ("Expressão preposicionada ligada ao nome",
                    "A ligação ao nome é a previsão do modelo; sua adequação precisa ser conferida no contexto.")
        return "Expressão preposicionada", "A função específica não foi determinada com segurança."
    if dependencia == "nsubj":
        return "Sujeito" if no else "Núcleo do sujeito", None
    if dependencia == "csubj":
        return "Núcleo de oração com função de sujeito", None
    if dependencia == "obj":
        # Não desfazer uma revisão de regência para exibir um objeto direto.
        revisada = leitura.get("funcao_sintatica", "")
        if "preposicionado" in revisada.lower():
            return revisada, None
        return "Objeto direto" if no else "Núcleo do objeto direto", None
    if dependencia == "iobj":
        return "Objeto indireto" if no else "Núcleo do objeto indireto", None
    return leitura.get("funcao_sintatica") or "Função não determinada", None


def _termos(oracao, palavras, texto):
    proprios = set(oracao["token_ids"])
    grupo = [t for t in oracao.get("grupo_verbal_token_ids", []) if t in proprios]
    conectivos = set(oracao.get("conectivo_token_ids", []))
    candidatos = [no for no in _nos(oracao.get("raizes", []))
                  if no.get("tipo") == "constituinte" and no.get("rotulo") in _FRASES
                  and set(no["token_ids"]) <= proprios
                  and not set(no["token_ids"]) & (set(grupo) | conectivos)]
    termos = []
    if grupo:
        copular = oracao.get("tipo_predicado") == "copular"
        termos.append({"texto": _texto_tokens(grupo, palavras, texto),
                       "tipo": "Predicação copular" if copular else "Grupo verbal" if len(grupo) > 1 else "Verbo",
                       "funcao": "Núcleo predicativo" if copular else "Núcleo verbal do predicado",
                       "inferido": False, "token_ids": list(grupo)})
    cobertos = set(grupo)
    for ident in oracao["token_ids"]:
        palavra = palavras[ident]
        if ident in cobertos or _base(palavra.get("dependencia")) not in _FUNCOES:
            continue
        # Um único núcleo externo evita atribuir a função de uma palavra a
        # um sintagma cujo analisador propõe várias cabeças incompatíveis.
        opcoes = [no for no in candidatos if no.get("nucleo_token_ids") == [ident]
                  and not set(no["token_ids"]) & cobertos]
        no = max(opcoes, key=lambda item: (len(item["token_ids"]), item["rotulo"] == "PP"), default=None)
        ids = list(no["token_ids"]) if no else [ident]
        funcao, observacao = _funcao(palavra, no, palavras)
        termo = {"texto": no["texto"] if no else palavra["texto"],
                 "tipo": _FRASES[no["rotulo"]] if no else _leitura(palavra).get("classe") or "Palavra",
                 "funcao": funcao, "inferido": False, "token_ids": ids}
        if observacao:
            termo["observacao"] = observacao
        termos.append(termo)
        cobertos.update(ids)
    ordem = {ident: numero for numero, ident in enumerate(palavras)}
    termos.sort(key=lambda termo: min(ordem[t] for t in termo["token_ids"]))
    nucleos = set(oracao.get("nucleo_token_ids", [])) | set(grupo)
    sujeito = any(_base(p.get("dependencia")) in {"nsubj", "csubj"}
                  and p.get("cabeca_token_id") in nucleos for p in palavras.values())
    finitos = [palavras[t] for t in grupo
               if _leitura(palavras[t]).get("pos") in {"VERB", "AUX"}
               and (_leitura(palavras[t]).get("morfologia") or {}).get("VerbForm") == "Fin"]
    if (not sujeito and len(finitos) == 1
            and primeira_pessoa_perfeito_verificada(finitos[0])):
        termos.insert(0, {"texto": "(eu)", "tipo": "Sujeito subentendido",
                          "funcao": "Sujeito", "inferido": True, "token_ids": [],
                          "observacao": "Identificado pela flexão de primeira pessoa do singular; não integra o texto nem a contagem de palavras."})
    return termos


def _morfologia(palavra):
    leitura = _leitura(palavra)
    atributos = leitura.get("morfologia") or {}
    partes = []
    if leitura.get("lema"):
        partes.append("lema: " + leitura["lema"])
    for chave, valores in _VALORES.items():
        if atributos.get(chave) in valores:
            partes.append(valores[atributos[chave]])
    linha = {"token_id": palavra["token_id"], "texto": palavra["texto"],
             "classe": leitura.get("classe") or "Classe não determinada",
             "resumo": "; ".join(partes), "lema": leitura.get("lema"),
             "morfologia": deepcopy(leitura.get("morfologia"))}
    if (unicodedata.normalize("NFC", palavra["texto"]).casefold() == "à"
            and palavra.get("pos") == "ADP" and _base(palavra.get("dependencia")) == "case"):
        linha["composicao"] = "preposição a + artigo definido a"
    return linha


def preparar_paineis(hierarquia, vista_revisada):
    """Enriquecer uma cópia da revisão com listas independentes por oração."""
    vista = deepcopy(vista_revisada)
    palavras = {p["token_id"]: p for p in vista["palavras"]}
    ordem = {ident: numero for numero, ident in enumerate(palavras)}
    vista["oracoes"].sort(key=lambda oracao: min(
        (ordem[ident] for ident in (oracao.get("token_ids_leitura") or oracao["token_ids"])),
        default=len(ordem)))
    texto = hierarquia["analise"]["anotacao"]["segmentacao"]["preparacao"]["trabalho"]["texto"]
    for numero, oracao in enumerate(vista["oracoes"], 1):
        oracao["numero"] = numero
        oracao["termos"] = _termos(oracao, palavras, texto)
        oracao["morfologia"] = [_morfologia(palavras[ident]) for ident in oracao["token_ids"]
                                if _lexical(palavras[ident]["texto"])]
    if not vista["oracoes"]:
        vista["morfologia_periodo"] = [_morfologia(palavra) for palavra in palavras.values()
                                       if _lexical(palavra["texto"])]
    return vista


def _contar_palavras(texto):
    # Unidades escritas, não tokens de análise: à, guarda-chuva e dizê-lo
    # contam uma vez; símbolos e pontuação isolados não contam como palavras.
    return sum(_lexical(trecho) for trecho in re.findall(r"\S+", texto))


def _hierarquias_validas(segmentacao, dados):
    valores = dados.values() if isinstance(dados, dict) else dados or []
    if isinstance(dados, dict) and ("hierarquia_id" in dados or "hierarquia" in dados):
        valores = [dados]
    resultado = {}
    for item in valores:
        if not isinstance(item, dict):
            continue
        registro = item.get("hierarquia", item)
        if not isinstance(registro, dict):
            continue
        origem = registro.get("analise", {}).get("anotacao", {}).get("segmentacao")
        # Uma execução de outro histórico não deve completar este parágrafo.
        if origem != segmentacao or not isinstance(registro.get("oracoes"), list):
            continue
        resultado.setdefault(registro.get("periodo_id"), registro)
    return resultado


def resumo_segmentacao(segmentacao, hierarquias):
    """Contagens literais e destaques, com totais oracionais só se completos.

    A contagem de palavras usa unidades separadas por espaço que contêm letra
    ou número Unicode. Caracteres são pontos de código do texto de trabalho,
    incluindo espaços e pontuação, sem normalização adicional.
    """
    registros = _hierarquias_validas(segmentacao, hierarquias)
    periodos = {p["id"]: p for p in segmentacao["periodos"]}
    resultado = {"paragrafos": {}, "periodos": {},
                 "metodo_contagem": "unidades_escritas_com_letra_ou_numero_separadas_por_espaco; caracteres_unicode_incluindo_espacos"}
    for periodo in periodos.values():
        registro = registros.get(periodo["id"])
        resultado["periodos"][periodo["id"]] = {
            "palavras": _contar_palavras(periodo["texto"]), "caracteres": len(periodo["texto"]),
            "oracoes": len(registro["oracoes"]) if registro is not None else None}
    for paragrafo in segmentacao["paragrafos"]:
        unidades = [periodos[ident] for ident in paragrafo["periodos"]]
        analisados = [registros[p["id"]] for p in unidades if p["id"] in registros]
        completo = len(analisados) == len(unidades)
        identificadas = sum(len(r["oracoes"]) for r in analisados)
        trechos = []
        inicio = paragrafo["trabalho"]["inicio"]
        cursor = inicio
        for numero, periodo in enumerate(unidades, 1):
            pi, pf = periodo["trabalho"]["inicio"], periodo["trabalho"]["fim"]
            if cursor < pi:
                trechos.append({"texto": paragrafo["texto"][cursor - inicio:pi - inicio]})
            trechos.append({"texto": paragrafo["texto"][pi - inicio:pf - inicio],
                           "periodo_id": periodo["id"], "ordem": numero})
            cursor = pf
        fim = paragrafo["trabalho"]["fim"]
        if cursor < fim:
            trechos.append({"texto": paragrafo["texto"][cursor - inicio:fim - inicio]})
        resultado["paragrafos"][paragrafo["id"]] = {
            "periodos": len(unidades), "palavras": _contar_palavras(paragrafo["texto"]),
            "caracteres": len(paragrafo["texto"]), "oracoes": identificadas if completo else None,
            "oracoes_identificadas": identificadas, "periodos_analisados": len(analisados),
            "completo": completo, "trechos": trechos}
    return resultado
