"""Arquivos e vetores fictícios para testar somente o consumidor.

Os JSONs foram congelados de uma execução do produtor da etapa 09 usando
anotações manuais e um adaptador determinístico. Nenhum modelo neural foi
executado. Este módulo adapta metadados e valores para os casos de teste sem
importar ou executar o aplicativo produtor.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import struct

from agente_analista.contratos_importacao import hash_json


PASTA = Path(__file__).resolve().parent / "fixtures"
INSTANTE = "2026-10-08T09:30:00-03:00"
TEXTO = "  Árvore\tverde.  Casa azul! \t Céu.\r\n\r\nOutro.  João saiu.  "
TIPOS_REPRESENTACAO = ("documento", "paragrafo", "contextual", "periodo")


def _ler(nome):
    return json.loads((PASTA / (nome + ".json")).read_text(encoding="utf-8"))


def carregar_perfil(perfil_id="e5_simetrico", *, opcoes=None):
    perfil = deepcopy(_ler("exportacao_atual")["perfil"])
    if perfil_id not in ("e5_simetrico", "e5_consulta", "e5_analista"):
        raise ValueError("Perfil não previsto nas fixtures do agente.")
    perfil.update(id=perfil_id, finalidade="consulta" if perfil_id == "e5_consulta" else "simetrico")
    if perfil_id == "e5_analista":
        perfil.update(finalidade="consulta", estrategia_consultas="paragrafos_documento",
                      limite_tokens=504, agregacao="nenhuma", texto_documento="original")
    perfil.update(deepcopy(opcoes or {}))
    return perfil


def contexto_teste(*, normalizar=False):
    return _ler("exportacao_atual_crlf" if normalizar else "exportacao_atual")["contexto"]


def _float32(valores):
    return list(struct.unpack("<" + "f" * len(valores), struct.pack("<" + "f" * len(valores), *valores)))


def _normalizar(valores):
    norma = math.hypot(*valores)
    return _float32([v / norma for v in valores])


def _espaco(perfil, processamento):
    campos_perfil = ("modelo", "tokenizador", "backend", "precisao_inferencia", "pooling", "normalizacao",
                     "formato_armazenamento", "fragmentacao", "agregacao")
    campos_proc = ("backend", "versao_backend", "modelo", "tokenizador", "dimensao", "pooling",
                   "precisao_inferencia", "bibliotecas", "limite_tokens", "limite_modelo_tokens")
    descricao = {"versao": "1.0.0", "perfil": {k: perfil[k] for k in campos_perfil},
                 "adaptador": {k: processamento[k] for k in campos_proc},
                 "prefixo": perfil["prefixo"], "finalidade": perfil["finalidade"],
                 "perfil_versao": perfil["versao"], "legado": perfil["legado"]}
    return "sha256:" + hash_json(descricao)


def construir_exportacao(*, normalizar=False, dimensao=1024, limite=160,
                        perfil_id="e5_simetrico", opcoes=None, fragmentar=False):
    """Adapte uma exportação congelada, sem depender de módulos do produtor.

    Limites 160/512 cobrem a fixture integral; 23 usa a exportação já recortada.
    A opção CRLF + fragmentação ainda não é um caso utilizado nesta suíte.
    """
    if limite not in (23, 160, 512) or (normalizar and limite == 23):
        raise ValueError("Combinação não prevista nas fixtures do agente.")
    if perfil_id == "e5_analista":
        limite = 512
        nome = "exportacao_analista_crlf" if normalizar else (
            "exportacao_analista_fragmentada" if fragmentar else "exportacao_analista")
    else:
        nome = "exportacao_atual_crlf" if normalizar else (
            "exportacao_atual_fragmentada" if limite == 23 else "exportacao_atual")
    registro = _ler(nome)
    perfil = carregar_perfil(perfil_id, opcoes=opcoes)
    return recalcular_exportacao(registro, perfil=perfil, dimensao=dimensao, limite=limite)


def recalcular_exportacao(registro, *, perfil=None, dimensao=None, limite=None):
    """Recrie identidades e bytes depois de adaptar uma exportação de teste.

    Não constrói textos, fragmentos ou origens: esses dados continuam os da
    referência congelada. Isso permite testar metadados adulterados mantendo
    consistentes os hashes que não são o objeto daquela verificação.
    """
    registro = deepcopy(registro)
    perfil = deepcopy(registro["perfil"] if perfil is None else perfil)
    registro["perfil"] = perfil
    proc = registro["processamento"]
    dimensao = proc["dimensao"] if dimensao is None else dimensao
    limite = proc["limite_modelo_tokens"] if limite is None else limite
    proc["dimensao"] = proc["adaptador"]["dimensao"] = dimensao
    proc["limite_tokens"] = perfil["limite_tokens"] or limite
    proc["limite_modelo_tokens"] = proc["adaptador"]["limite_tokens"] = limite
    for campo in ("modelo", "tokenizador", "backend", "pooling", "precisao_inferencia"):
        proc[campo] = deepcopy(perfil[campo])
        proc["adaptador"][campo] = deepcopy(perfil[campo])
    cfg = hash_json({"perfil": perfil, "processamento": proc, "modulo_versao": "1.0.0"})
    registro["configuracao_sha256"] = cfg
    registro["espaco_vetorial_id"] = _espaco(perfil, proc)
    substituidos, por_id = {}, {}
    for artefato in registro["artefatos"]:
        antigo_id = artefato["id"]
        artefato["dimensao"] = dimensao
        artefato["configuracao_sha256"] = cfg
        if "entrada" in artefato:
            entrada = artefato["entrada"]
            artefato["chave_cache"] = hash_json({"versao": "1.0.0", "configuracao_sha256": cfg,
                                               "entrada_sha256": entrada["sha256"],
                                               "tokens_sha256": entrada["sha256_tokens"]})
            valores = [float((sum(map(ord, entrada["texto"])) + i * 17) % 101 + 1)
                       for i in range(dimensao)]
            artefato["valores"] = _normalizar(valores)
        else:
            derivacao = artefato["derivacao"]
            derivacao["componentes_ids"] = [substituidos[i] for i in derivacao["componentes_ids"]]
            artefato["chave_cache"] = hash_json({"configuracao_sha256": cfg, "derivacao": derivacao})
            total = math.fsum(derivacao["pesos"])
            media = [math.fsum(por_id[i]["valores"][j] * (peso / total)
                              for i, peso in zip(derivacao["componentes_ids"], derivacao["pesos"]))
                     for j in range(dimensao)]
            artefato["valores"] = _normalizar(media)
        dados = struct.pack("<" + "f" * dimensao, *artefato["valores"])
        artefato["sha256_bytes"] = hashlib.sha256(dados).hexdigest()
        artefato["id"] = "sha256:" + hash_json({k: v for k, v in artefato.items() if k != "id"})
        substituidos[antigo_id] = artefato["id"]
        por_id[artefato["id"]] = artefato
    for rep in registro["representacoes"]:
        if rep["artefato_id"] is not None:
            rep["artefato_id"] = substituidos[rep["artefato_id"]]
        for fragmento in rep["fragmentos"]:
            fragmento["artefato_id"] = substituidos[fragmento["artefato_id"]]
    return registro


def construir_exportacao_selecao(tipos, *, normalizar=False, dimensao=1024,
                                fragmentar=False, texto_documento="trabalho", limite=None):
    """Escolha representações de uma referência ALL4 do produtor 39ae237.

    Os casos de seleção inválida devem adulterar o perfil posteriormente e
    chamar recalcular_exportacao; a escolha normal gera ordem canônica.
    """
    if (type(tipos) is not list or not tipos
            or any(type(tipo) is not str or tipo not in TIPOS_REPRESENTACAO for tipo in tipos)
            or len(set(tipos)) != len(tipos)):
        raise ValueError("Escolha tipos válidos, únicos e não vazios na fixture.")
    if texto_documento not in ("trabalho", "original"):
        raise ValueError("Campo de documento não previsto na fixture.")
    if fragmentar:
        if texto_documento != "trabalho" or not normalizar:
            raise ValueError("A referência fragmentada declara CRLF normalizado e documento de trabalho.")
        nome = "exportacao_selecao_fragmentada"
    elif normalizar:
        nome = "exportacao_selecao_original" if texto_documento == "original" else "exportacao_selecao_crlf"
    else:
        # Sem normalização, texto e mapas original/trabalho coincidem; a
        # referência usa trabalho para preservar exatamente a saída congelada.
        if texto_documento != "trabalho":
            raise ValueError("A referência de documento original exige normalizar=True.")
        nome = "exportacao_selecao_todos"
    registro = _ler(nome)
    registro["perfil"]["tipos_representacao"] = [tipo for tipo in TIPOS_REPRESENTACAO if tipo in tipos]
    registro["representacoes"] = [rep for rep in registro["representacoes"] if rep["tipo"] in tipos]
    usados = set()
    for ordem, rep in enumerate(registro["representacoes"]):
        rep.update(id=f"{registro['execucao_id']}:representacao:{ordem}", ordem=ordem)
        usados.add(rep["artefato_id"])
        usados.update(fragmento["artefato_id"] for fragmento in rep["fragmentos"])
    registro["artefatos"] = [artefato for artefato in registro["artefatos"] if artefato["id"] in usados]
    registro["validacao"]["cobertura"].update(
        representacoes_total=len(registro["representacoes"]), artefatos_total=len(registro["artefatos"]),
    )
    return recalcular_exportacao(registro, dimensao=dimensao, limite=limite)
