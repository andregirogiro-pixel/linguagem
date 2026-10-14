"""Camada de leitura revisada, separada das previsões e registros originais.

É aplicada também a execuções históricas, sem nova inferência e sem gravar
sobre o JSON validado. O download original continua disponível para auditoria.
"""

from analise_gramatical import revisar_palavras
from visualizacao_hierarquia import preparar_visualizacao


VERSAO_REVISAO = "1.0.0"
_TRANSITIVOS_DIRETOS = {
    "perceber", "notar", "afirmar", "dizer", "achar", "ver", "ouvir",
    "descobrir", "reconhecer", "entender", "saber",
}


def _leitura(palavra):
    return palavra.get("revisao", palavra)


def preparar_revisao(hierarquia):
    """Devolve dados reutilizáveis de apresentação com a revisão identificada."""
    vista = preparar_visualizacao(hierarquia)
    vista["palavras"] = revisar_palavras(vista["palavras"])
    palavras = {p["token_id"]: p for p in vista["palavras"]}
    oracoes = {o["id"]: o for o in vista["oracoes"]}
    ordem = {p["token_id"]: i for i, p in enumerate(vista["palavras"])}
    vista["processamento_revisao"] = {
        "identificacao": "revisao_gramatical_e_visualizacao",
        "versao": VERSAO_REVISAO,
        "preserva_previsoes_originais": True,
        "escopo": "regras_conservadoras_com_evidencias_locais",
    }
    for oracao in vista["oracoes"]:
        conectivos = [palavras[t]["texto"].casefold() for t in oracao.get("conectivo_token_ids", [])]
        dep = oracao.get("dependencia", "").lower().split(":", 1)[0]
        if dep == "advcl" and conectivos == ["quando"]:
            oracao["classificacao"] = "Oração subordinada adverbial temporal"
            oracao["justificativa"] = (
                "Classificação pela relação adverbial com a oração principal "
                "e pelo marcador temporal ‘quando’."
            )
        elif dep == "ccomp" and conectivos == ["que"]:
            pai = oracoes.get(oracao.get("pai_oracao_id"))
            if pai:
                nucleos = [_leitura(palavras[t]) for t in pai["nucleo_token_ids"]]
                inicio = min((ordem[t] for t in oracao.get("token_ids", [])), default=-1)
                anterior = vista["palavras"][inicio - 1] if inicio > 0 else None
                tem_preposicao = anterior and _leitura(anterior).get("pos") == "ADP"
                if any(p.get("lema") in _TRANSITIVOS_DIRETOS for p in nucleos) and not tem_preposicao:
                    oracao["classificacao"] = "Oração subordinada substantiva objetiva direta"
                    oracao["justificativa"] = (
                        "Complemento oracional introduzido por ‘que’, sem preposição, "
                        "ligado a um verbo de regência direta reconhecida pela regra."
                    )
        grupo = [palavras[t] for t in oracao.get("grupo_verbal_token_ids", [])]
        participio = any(_leitura(p).get("morfologia", {}).get("VerbForm") == "Part" for p in grupo)
        auxiliar = any(
            _leitura(p).get("lema") in {"haver", "ter"}
            and _leitura(p).get("morfologia", {}).get("Tense") == "Imp"
            and _leitura(p).get("morfologia", {}).get("Mood") == "Ind"
            for p in grupo
        )
        if participio and auxiliar:
            oracao["tempo_composto"] = "Pretérito mais-que-perfeito composto do indicativo"
    return vista
