"""O consumidor aceita escolhas exportadas sem executar o produtor ou modelos."""

from collections import Counter
from copy import deepcopy
from itertools import combinations
import json
import subprocess
import sys
import unittest

from fixtures_importacao import (
    PASTA, TEXTO, TIPOS_REPRESENTACAO, construir_exportacao,
    construir_exportacao_selecao, recalcular_exportacao,
)
from agente_analista.importacao import ErroImportacao, importar_vetorizacao


class SelecaoImportadaTests(unittest.TestCase):
    def test_referencias_all4_coincidem_com_exportacoes_congeladas_do_produtor(self):
        variantes = (
            ("exportacao_selecao_todos", {}),
            ("exportacao_selecao_crlf", {"normalizar": True}),
            ("exportacao_selecao_original", {"normalizar": True, "texto_documento": "original"}),
            ("exportacao_selecao_fragmentada", {"normalizar": True, "fragmentar": True}),
        )
        for nome, opcoes in variantes:
            with self.subTest(nome=nome):
                esperado = json.loads((PASTA / (nome + ".json")).read_text(encoding="utf-8"))
                construido = construir_exportacao_selecao(list(TIPOS_REPRESENTACAO), dimensao=3, **opcoes)
                self.assertEqual(construido, esperado)
                self.assertEqual(importar_vetorizacao(esperado)["registro"], esperado)

    def test_importa_as_quinze_combinacoes_com_tipos_contagens_textos_e_vetores_exatos(self):
        quantidades = {"documento": 1, "paragrafo": 2, "contextual": 5, "periodo": 5}
        espacos = set()
        for tamanho in range(1, 5):
            for tipos in combinations(TIPOS_REPRESENTACAO, tamanho):
                with self.subTest(tipos=tipos):
                    registro = construir_exportacao_selecao(list(tipos), dimensao=3)
                    antes = deepcopy(registro)
                    importado = importar_vetorizacao(registro)
                    esperado = [tipo for tipo in tipos for _ in range(quantidades[tipo])]
                    self.assertEqual([consulta["tipo"] for consulta in importado["consultas"]], esperado)
                    self.assertEqual(Counter(c["tipo"] for c in importado["consultas"]),
                                     {tipo: quantidades[tipo] for tipo in tipos})
                    self.assertEqual(importado["relato"]["texto"], TEXTO)
                    por_id = {artefato["id"]: artefato for artefato in registro["artefatos"]}
                    for consulta, rep in zip(importado["consultas"], registro["representacoes"]):
                        self.assertEqual(consulta["origem"], rep["origem"])
                        self.assertEqual(consulta["texto"], TEXTO[consulta["inicio"]:consulta["fim"]])
                        self.assertEqual(consulta["texto_vetorizado"], consulta["texto"])
                        self.assertEqual(consulta["vetor"], por_id[rep["artefato_id"]]["valores"])
                    espacos.add(importado["fonte"]["espaco_vetorial_id"])
                    self.assertEqual(registro, antes)
                    self.assertEqual(importado["registro"], antes)
        self.assertEqual(len(espacos), 1)

    def test_paragrafos_preservam_ids_da_segmentacao_unicode_e_mapas_crlf(self):
        registro = construir_exportacao_selecao(["paragrafo"], normalizar=True, dimensao=3)
        importado = importar_vetorizacao(registro)
        esperado = ["Árvore\tverde.  Casa azul! \t Céu.", "Outro.  João saiu."]
        self.assertEqual([c["texto_vetorizado"] for c in importado["consultas"]], esperado)
        self.assertEqual([c["texto"] for c in importado["consultas"]], esperado)
        origem = [c["origem"] for c in importado["consultas"]]
        self.assertEqual([o["campo"] for o in origem], ["segmentacao.paragrafos.texto"] * 2)
        self.assertEqual([o["paragrafo_id"] for o in origem], [
            "segmentacao-fixture-07:paragrafo:0", "segmentacao-fixture-07:paragrafo:1",
        ])
        self.assertEqual(origem[1]["trabalho"], {"inicio": 36, "fim": 54})
        self.assertEqual(origem[1]["original"], {"inicio": 38, "fim": 56})
        self.assertEqual(importado["relato"]["texto"], TEXTO)

    def test_documento_respeita_escolha_original_ou_trabalho_com_crlf(self):
        for campo in ("trabalho", "original"):
            with self.subTest(campo=campo):
                registro = construir_exportacao_selecao(
                    ["documento"], normalizar=True, texto_documento=campo, dimensao=3,
                )
                importado = importar_vetorizacao(registro)
                self.assertEqual(len(importado["consultas"]), 1)
                consulta = importado["consultas"][0]
                self.assertEqual(consulta["origem"]["campo"], f"preparacao.{campo}.texto")
                self.assertEqual(consulta["texto"], TEXTO)
                self.assertEqual(consulta["texto_vetorizado"], TEXTO if campo == "original" else TEXTO.replace("\r\n", "\n"))
                self.assertEqual((consulta["inicio"], consulta["fim"]), (0, len(TEXTO)))

    def test_fragmentacao_importa_agregados_exportados_sem_recalcular_consultas(self):
        registro = construir_exportacao_selecao(
            ["documento", "paragrafo"], normalizar=True, fragmentar=True, dimensao=3,
        )
        importado = importar_vetorizacao(registro)
        self.assertEqual([c["tipo"] for c in importado["consultas"]], ["documento", "paragrafo", "paragrafo"])
        por_id = {artefato["id"]: artefato for artefato in registro["artefatos"]}
        self.assertGreater(len(registro["artefatos"]), len(importado["consultas"]))
        for consulta, rep in zip(importado["consultas"], registro["representacoes"]):
            self.assertEqual(rep["metodo"], "agregado")
            self.assertGreater(len(rep["fragmentos"]), 1)
            self.assertIn("derivacao", por_id[rep["artefato_id"]])
            self.assertEqual(consulta["vetor"], por_id[rep["artefato_id"]]["valores"])
            textos = [por_id[f["artefato_id"]]["entrada"]["texto"][len("query: "):]
                      for f in rep["fragmentos"]]
            self.assertEqual("".join(textos), consulta["texto_vetorizado"])

    def test_perfil_rejeita_selecao_vazia_invalida_repetida_e_nao_canonica_com_hashes_validos(self):
        registro = construir_exportacao_selecao(list(TIPOS_REPRESENTACAO), dimensao=3)
        invalidos = ([], None, "documento", {}, [True], [None], [[]],
                     ["relato"], ["contexto"], ["parágrafo"],
                     ["documento", "documento"], list(reversed(TIPOS_REPRESENTACAO)))
        for tipos in invalidos:
            with self.subTest(tipos=tipos):
                adulterado = deepcopy(registro)
                adulterado["perfil"]["tipos_representacao"] = tipos
                adulterado = recalcular_exportacao(adulterado)
                with self.assertRaises(ErroImportacao):
                    importar_vetorizacao(adulterado)

    def test_cobertura_rejeita_representacoes_ausentes_extras_ou_de_tipo_nao_escolhido(self):
        registro = construir_exportacao_selecao(["documento", "paragrafo"], dimensao=3)
        for alterar in (
            lambda r: r["representacoes"].pop(),
            lambda r: r["representacoes"].append(deepcopy(r["representacoes"][-1])),
            lambda r: r["representacoes"].reverse(),
        ):
            adulterado = deepcopy(registro)
            alterar(adulterado)
            adulterado["validacao"]["cobertura"]["representacoes_total"] = len(adulterado["representacoes"])
            with self.assertRaises(ErroImportacao):
                importar_vetorizacao(adulterado)
        # O campo seletor deve limitar as representações mesmo que o restante
        # do registro conserve hashes e artefatos consistentes.
        adulterado = deepcopy(registro)
        adulterado["perfil"]["tipos_representacao"] = ["documento"]
        with self.assertRaises(ErroImportacao):
            importar_vetorizacao(recalcular_exportacao(adulterado))

    def test_paragrafo_rejeita_origem_id_texto_e_mapa_adulterados(self):
        registro = construir_exportacao_selecao(["paragrafo"], normalizar=True, dimensao=3)
        mutacoes = (
            lambda r: r["representacoes"][0]["origem"].update(paragrafo_id="P1"),
            lambda r: r["representacoes"][0]["origem"].update(campo="preparacao.original.texto"),
            lambda r: r["representacoes"][1]["origem"]["original"].update(inicio=36),
            lambda r: r["representacoes"][1]["origem"]["trabalho"].update(inicio=38),
            lambda r: r["representacoes"][0].update(texto_sha256="0" * 64),
        )
        for indice, alterar in enumerate(mutacoes):
            with self.subTest(mutacao=indice):
                adulterado = deepcopy(registro)
                alterar(adulterado)
                with self.assertRaises(ErroImportacao):
                    importar_vetorizacao(adulterado)

    def test_fragmentos_bytes_e_hashes_adulterados_permanecem_rejeitados(self):
        registro = construir_exportacao_selecao(
            ["documento", "paragrafo"], normalizar=True, fragmentar=True, dimensao=3,
        )
        mutacoes = (
            lambda r: r["artefatos"][0]["valores"].__setitem__(0, 0.0),
            lambda r: r["artefatos"][0].update(sha256_bytes="0" * 64),
            lambda r: r["artefatos"][0].update(id="sha256:" + "0" * 64),
            lambda r: r["representacoes"][0]["fragmentos"].pop(),
            lambda r: r["representacoes"][0]["fragmentos"][0]["original"].update(inicio=1),
            lambda r: r["artefatos"][-1]["derivacao"]["pesos"].__setitem__(0, 1),
        )
        for indice, alterar in enumerate(mutacoes):
            with self.subTest(mutacao=indice):
                adulterado = deepcopy(registro)
                alterar(adulterado)
                with self.assertRaises(ErroImportacao):
                    importar_vetorizacao(adulterado)

    def test_omissao_do_campo_preserva_importacao_legada_e5_simetrico_e_analista(self):
        for perfil, esperado in (
            ("e5_simetrico", ["periodo", "contextual"] * 5 + ["documento"]),
            ("e5_analista", ["paragrafo", "paragrafo", "documento"]),
        ):
            with self.subTest(perfil=perfil):
                registro = construir_exportacao(perfil_id=perfil, dimensao=3, normalizar=True)
                self.assertNotIn("tipos_representacao", registro["perfil"])
                importado = importar_vetorizacao(registro)
                self.assertEqual([c["tipo"] for c in importado["consultas"]], esperado)
                if perfil == "e5_analista":
                    self.assertEqual([c["paragrafo_id"] for c in importado["consultas"]], ["P1", "P2", None])

    def test_selecao_importa_em_processo_sem_bibliotecas_neurais_ou_modulos_do_produtor(self):
        registros = [
            construir_exportacao_selecao(["documento"], dimensao=3),
            construir_exportacao_selecao(["paragrafo"], normalizar=True, dimensao=3),
            construir_exportacao_selecao(list(TIPOS_REPRESENTACAO), normalizar=True, fragmentar=True, dimensao=3),
        ]
        bloqueados = ["torch", "transformers", "spacy", "sentence_transformers", "huggingface_hub",
                      "app", "preparacao", "segmentacao", "anotacao", "sintaxe_entidades",
                      "regras_linguisticas", "unidades_contexto", "vetorizacao", "perfis_vetorizacao", "modelo_embeddings"]
        script = """
import builtins,json,sys
bloqueados=set(json.loads(sys.argv[1]))
original=builtins.__import__
def importar(nome,*args,**kwargs):
    if nome.split('.',1)[0] in bloqueados:
        raise AssertionError('Import indevido: '+nome)
    return original(nome,*args,**kwargs)
builtins.__import__=importar
from agente_analista.importacao import importar_vetorizacao
for registro in json.load(sys.stdin):
    recebido=importar_vetorizacao(registro)
    assert [c['tipo'] for c in recebido['consultas']]==[r['tipo'] for r in registro['representacoes']]
assert not bloqueados.intersection(sys.modules)
print('Importação independente validada.')
"""
        resultado = subprocess.run(
            [sys.executable, "-c", script, json.dumps(bloqueados)],
            input=json.dumps(registros), text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(resultado.returncode, 0, resultado.stdout + resultado.stderr)
        self.assertIn("Importação independente validada.", resultado.stdout)


if __name__ == "__main__":
    unittest.main()
