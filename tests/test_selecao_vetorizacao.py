"""Seleção de unidades vetoriais com origem manual e sem pesos de modelos."""

from collections import Counter
from copy import deepcopy
import hashlib
from itertools import combinations
import unittest

from fixtures_contexto import construir_regras, INSTANTE, TEXTO
from perfis_vetorizacao import ErroPerfil, carregar_perfil, validar_perfil
from test_vetorizacao import AdaptadorTeste
from unidades_contexto import construir_unidades_contexto
from vetorizacao import (
    ErroVetorizacao, validar_vetorizacao, vetorizar_contexto,
    verificar_compatibilidade,
)


TIPOS = ("documento", "paragrafo", "contextual", "periodo")


class SelecaoVetorizacaoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexto = construir_unidades_contexto(
            construir_regras(), execucao_id="contexto-selecao", registrado_em=INSTANTE,
        )

    def gerar(self, tipos=None, *, contexto=None, limite=8192):
        opcoes = None if tipos is None else {"tipos_representacao": tipos}
        perfil = carregar_perfil(opcoes=opcoes)
        adaptador = AdaptadorTeste(perfil, limite=limite)
        registro = vetorizar_contexto(
            contexto or self.contexto, opcoes=opcoes, adaptador=adaptador,
            execucao_id="vetores-selecao", registrado_em=INSTANTE,
        )
        return registro, adaptador

    def test_todas_as_quinze_combinacoes_geram_somente_os_tipos_selecionados(self):
        quantidades = {"documento": 1, "paragrafo": 2, "contextual": 5, "periodo": 5}
        fonte = deepcopy(self.contexto)
        for tamanho in range(1, 5):
            for selecao in combinations(TIPOS, tamanho):
                with self.subTest(selecao=selecao):
                    # A ordem enviada não deve desorganizar os grupos resultantes.
                    registro, adaptador = self.gerar(list(reversed(selecao)))
                    esperado = [tipo for tipo in TIPOS if tipo in selecao]
                    self.assertEqual(registro["perfil"]["tipos_representacao"], esperado)
                    self.assertEqual(
                        [rep["tipo"] for rep in registro["representacoes"]],
                        [tipo for tipo in esperado for _ in range(quantidades[tipo])],
                    )
                    self.assertEqual(
                        Counter(rep["tipo"] for rep in registro["representacoes"]),
                        {tipo: quantidades[tipo] for tipo in selecao},
                    )
                    por_id = {art["id"]: art for art in registro["artefatos"]}
                    entradas_representadas = set()
                    for rep in registro["representacoes"]:
                        self.assertTrue(rep["cobertura_integral"])
                        self.assertEqual(rep["omissoes"], [])
                        self.assertEqual(rep["metodo"], "direto")
                        art = por_id[rep["artefato_id"]]
                        posicao = rep["origem"]["trabalho"]
                        texto = TEXTO[posicao["inicio"]:posicao["fim"]]
                        self.assertEqual(art["entrada"]["texto"], "query: " + texto)
                        self.assertEqual(rep["texto_sha256"], hashlib.sha256(texto.encode()).hexdigest())
                        entradas_representadas.add(art["entrada"]["texto"])
                    self.assertEqual(
                        {texto for chamada in adaptador.chamadas for texto in chamada},
                        entradas_representadas,
                    )
                    self.assertTrue(validar_vetorizacao(registro)["pronto"])
                    self.assertEqual(registro["contexto"], fonte)
        self.assertEqual(self.contexto, fonte)

    def test_omissao_preserva_ordem_e_perfil_legados_sem_paragrafos(self):
        registro, _ = self.gerar()
        self.assertNotIn("tipos_representacao", carregar_perfil())
        self.assertNotIn("tipos_representacao", registro["perfil"])
        self.assertEqual(
            [rep["tipo"] for rep in registro["representacoes"]],
            ["periodo", "contextual"] * 5 + ["documento"],
        )
        self.assertTrue(validar_vetorizacao(registro)["pronto"])

    def test_paragrafos_preservam_unicode_crlf_e_mapas_apos_normalizacao(self):
        for normalizar in (False, True):
            with self.subTest(normalizar=normalizar):
                contexto = construir_unidades_contexto(
                    construir_regras(normalizar=normalizar), registrado_em=INSTANTE,
                )
                registro, _ = self.gerar(["paragrafo"], contexto=contexto, limite=23)
                segmentacao = contexto["regras"]["analise"]["anotacao"]["segmentacao"]
                self.assertIn("\r\n\r\n", segmentacao["preparacao"]["original"]["texto"])
                por_id = {art["id"]: art for art in registro["artefatos"]}
                textos = []
                for rep, paragrafo in zip(registro["representacoes"], segmentacao["paragrafos"]):
                    self.assertEqual(rep["origem"], {
                        "campo": "segmentacao.paragrafos.texto",
                        "paragrafo_id": paragrafo["id"],
                        "trabalho": paragrafo["trabalho"], "original": paragrafo["original"],
                    })
                    texto = "".join(
                        por_id[fragmento["artefato_id"]]["entrada"]["texto"][len("query: "):]
                        for fragmento in rep["fragmentos"]
                    )
                    textos.append(texto)
                    self.assertEqual(rep["texto_sha256"], hashlib.sha256(texto.encode()).hexdigest())
                    self.assertTrue(rep["cobertura_integral"])
                self.assertEqual(textos, ["Árvore\tverde.  Casa azul! \t Céu.", "Outro.  João saiu."])
                self.assertEqual(registro["representacoes"][1]["origem"]["original"], {"inicio": 38, "fim": 56})
                self.assertEqual(
                    registro["representacoes"][1]["origem"]["trabalho"],
                    {"inicio": 36, "fim": 54} if normalizar else {"inicio": 38, "fim": 56},
                )
                self.assertTrue(validar_vetorizacao(registro)["pronto"])

    def test_selecao_invalida_e_repeticoes_sao_rejeitadas(self):
        invalidos = ([], None, "documento", True, 1, {}, [None], [True], [[]],
                     ["relato"], ["parágrafo"], ["documento", "documento"], ["periodo", "contexto"])
        for tipos in invalidos:
            with self.subTest(tipos=tipos), self.assertRaises(ErroPerfil):
                carregar_perfil(opcoes={"tipos_representacao": tipos})
        perfil = carregar_perfil(opcoes={"tipos_representacao": ["periodo", "documento"]})
        self.assertEqual(perfil["tipos_representacao"], ["documento", "periodo"])
        adulterado = deepcopy(perfil)
        adulterado["tipos_representacao"].reverse()
        with self.assertRaises(ErroPerfil):
            validar_perfil(adulterado)

    def test_selecao_nao_muda_espaco_vetorial_nem_comparabilidade(self):
        legado, _ = self.gerar()
        relato, _ = self.gerar(["documento"])
        paragrafos, _ = self.gerar(["paragrafo"])
        todos, _ = self.gerar(list(TIPOS))
        self.assertEqual(len({r["espaco_vetorial_id"] for r in (legado, relato, paragrafos, todos)}), 1)
        self.assertEqual(len({r["configuracao_sha256"] for r in (legado, relato, paragrafos, todos)}), 4)
        self.assertTrue(verificar_compatibilidade(relato, paragrafos)["compativel"])
        self.assertTrue(verificar_compatibilidade(legado, todos)["compativel"])

    def test_manifesto_adulterado_nao_pode_inventar_ou_ocultar_representacoes(self):
        registro, _ = self.gerar(["documento", "paragrafo"])
        mutacoes = (
            lambda r: r["representacoes"].pop(),
            lambda r: r["representacoes"].append(deepcopy(r["representacoes"][-1])),
            lambda r: r["representacoes"].reverse(),
            lambda r: r["representacoes"][1]["origem"].update(paragrafo_id="paragrafo-estrangeiro"),
            lambda r: r["representacoes"][1]["origem"]["original"].update(inicio=0),
            lambda r: r["perfil"].update(tipos_representacao=["documento"]),
        )
        for indice, mutacao in enumerate(mutacoes):
            with self.subTest(mutacao=indice):
                adulterado = deepcopy(registro)
                mutacao(adulterado)
                with self.assertRaises(ErroVetorizacao):
                    validar_vetorizacao(adulterado)


if __name__ == "__main__":
    unittest.main()
