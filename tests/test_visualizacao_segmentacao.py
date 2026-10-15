"""Projeção de leitura com gabaritos manuais, sem carregar modelo linguístico."""

import copy
import json
import unittest
from unittest.mock import patch

from fixtures_regras import CASOS, INSTANTE, construir_analise, ids
from regras_linguisticas import aplicar_regras_linguisticas
from visualizacao_segmentacao import construir_visualizacao_segmentacao


class VisualizacaoSegmentacaoTests(unittest.TestCase):
    def visualizar(self, nome="desejo", *, fonte=None, habilitadas=None):
        fonte = fonte if fonte is not None else construir_analise(nome)
        argumentos = {"execucao_id": "regras-mapa", "registrado_em": INSTANTE}
        if habilitadas is not None:
            argumentos["regras_habilitadas"] = habilitadas
        regras = aplicar_regras_linguisticas(fonte, **argumentos)
        anotacao = fonte["anotacao"]
        segmentacao = anotacao["segmentacao"]
        return construir_visualizacao_segmentacao(segmentacao, anotacao, fonte, regras)

    @staticmethod
    def oracoes(mapa):
        return [oracao for paragrafo in mapa["paragraphs"]
                for periodo in paragrafo["periods"] for oracao in periodo["clauses"]]

    def test_alcance_descontinuo_preserva_fragmentos_e_nao_copia_sujeito(self):
        fonte = construir_analise("desejo")
        mapa = self.visualizar(fonte=fonte)
        principal, subordinada = self.oracoes(mapa)
        self.assertEqual(principal["fragments"], ["Ela quer", "."])
        self.assertEqual(principal["text"], "Ela quer […] .")
        self.assertEqual(subordinada["fragments"], ["sair"])
        self.assertEqual([t["id"] for t in principal["tokens"]], ids(fonte, [0, 1, 3]))
        self.assertEqual([t["id"] for t in subordinada["tokens"]], ids(fonte, [2]))
        self.assertTrue(all(o["scope_resolved"] for o in (principal, subordinada)))
        self.assertEqual(mapa["clauses_total"], 2)

    def test_cabeca_externa_e_relacao_apontam_para_oracao_correta(self):
        fonte = construir_analise("desejo")
        principal, subordinada = self.oracoes(self.visualizar(fonte=fonte))
        sair, = subordinada["tokens"]
        self.assertEqual((sair["head_id"], sair["head_text"]), (ids(fonte, [1])[0], "quer"))
        self.assertTrue(sair["head_outside_clause"])
        self.assertFalse(sair["head_is_self"])
        quer = next(t for t in principal["tokens"] if t["text"] == "quer")
        self.assertTrue(quer["head_is_self"])
        self.assertFalse(quer["head_outside_clause"])
        self.assertTrue(any(r["anchor"] == principal["anchor"] and r["number"] == principal["number"]
                            for r in subordinada["relations"]))
        self.assertNotEqual(principal["anchor"], subordinada["anchor"])

    def test_coordenacao_mantem_sujeito_somente_onde_anotado(self):
        primeira, segunda = self.oracoes(self.visualizar("coordenacao"))
        self.assertEqual([t["text"] for t in primeira["tokens"]], ["Ela", "estudou", "."])
        self.assertEqual([t["text"] for t in segunda["tokens"]], ["e", "passou"])
        self.assertEqual([t["text"] for t in segunda["tokens"] if t["dependency"] == "nsubj"], [])
        self.assertTrue(any(r["anchor"] == primeira["anchor"] for r in segunda["relations"]))

    def test_copula_preserva_nucleo_adjetival_e_dependencia(self):
        oracao, = self.oracoes(self.visualizar("copula"))
        self.assertEqual(oracao["nucleus_text"], "feliz")
        feliz = next(t for t in oracao["tokens"] if t["text"] == "feliz")
        esta = next(t for t in oracao["tokens"] if t["text"] == "está")
        self.assertEqual(feliz["pos"], "ADJ")
        self.assertEqual(esta["dependency"], "cop")
        self.assertEqual(esta["head_text"], "feliz")

    def test_morfologia_nao_resolvida_difere_de_atributos_examinados_ausentes(self):
        linhas = copy.deepcopy(CASOS["desejo"]["tokens"])
        forma, lema, pos, cabeca, dep, _ = linhas[2]
        linhas[2] = (forma, lema, pos, cabeca, dep, None)
        mapa = self.visualizar(fonte=construir_analise("desejo", linhas=linhas))
        tokens = {t["text"]: t for o in self.oracoes(mapa) for t in o["tokens"]}
        self.assertIsNone(tokens["sair"]["morphology"])
        self.assertEqual(tokens["Ela"]["morphology"], [])
        finito = tokens["quer"]["morphology"]
        self.assertTrue(all({"label", "value", "field", "raw_value"} <= set(item) for item in finito))
        self.assertIn(("VerbForm", "Fin"), [(item["field"], item["raw_value"]) for item in finito])
        self.assertEqual(tokens["quer"]["lemma"], "querer")

    def test_pendencias_apontam_para_proxima_etapa_sem_inventar_oracoes(self):
        fonte = construir_analise("desejo")
        anotacao = fonte["anotacao"]
        segmentacao = anotacao["segmentacao"]
        casos = ((None, None, "morfologia", False, False),
                 (anotacao, None, "sintaxe", True, False),
                 (anotacao, fonte, "regras", True, True))
        for morfologia, sintaxe, etapa, tem_morfo, tem_sintaxe in casos:
            with self.subTest(etapa=etapa):
                mapa = construir_visualizacao_segmentacao(segmentacao, morfologia, sintaxe)
                self.assertEqual((mapa["clause_state"], mapa["next_stage"]), ("pendente", etapa))
                self.assertEqual(mapa["has_morphology"], tem_morfo)
                self.assertEqual(mapa["has_syntax"], tem_sintaxe)
                self.assertEqual(self.oracoes(mapa), [])
                self.assertTrue(mapa["clause_message"])
                self.assertEqual(len(mapa["paragraphs"][0]["periods"][0]["tokens"]), 4)

    def test_execucao_sem_oracoes_nao_se_confunde_com_regra_desabilitada(self):
        executada = self.visualizar("nominal", habilitadas=["oracao_predicado"])
        desabilitada = self.visualizar("nominal", habilitadas=[])
        self.assertEqual(executada["clause_state"], "executada")
        self.assertEqual(desabilitada["clause_state"], "desabilitada")
        self.assertNotEqual(executada["clause_message"], desabilitada["clause_message"])
        for mapa in (executada, desabilitada):
            self.assertEqual(mapa["clauses_total"], 0)
            self.assertEqual(self.oracoes(mapa), [])

    def test_regra_falhou_ou_foi_impedida_nao_anuncia_zero_ocorrencias(self):
        with patch("regras_linguisticas.detectar_regra", side_effect=RuntimeError("falha controlada")):
            falhou = self.visualizar(habilitadas=["oracao_predicado"])
        linhas = [(forma, lema, None, cabeca, dep, morfo)
                  for forma, lema, pos, cabeca, dep, morfo in CASOS["desejo"]["tokens"]]
        impedida = self.visualizar(fonte=construir_analise("desejo", linhas=linhas),
                                   habilitadas=["oracao_predicado"])
        self.assertEqual(falhou["clause_state"], "falhou")
        self.assertEqual(impedida["clause_state"], "impedida")
        for mapa in (falhou, impedida):
            self.assertEqual(self.oracoes(mapa), [])
            self.assertTrue(mapa["clause_message"])
        self.assertTrue(impedida["rule_notes"])

    def test_pos_ausente_na_fronteira_nao_expande_alcance_nulo_para_periodo(self):
        linhas = copy.deepcopy(CASOS["desejo"]["tokens"])
        forma, lema, pos, cabeca, dep, morfo = linhas[2]
        linhas[2] = (forma, lema, None, cabeca, dep, morfo)
        fonte = construir_analise("desejo", linhas=linhas)
        mapa = self.visualizar(fonte=fonte, habilitadas=["oracao_predicado"])
        oracao, = self.oracoes(mapa)
        self.assertEqual(mapa["clause_state"], "executada_com_pendencias")
        self.assertFalse(oracao["scope_resolved"])
        self.assertEqual(oracao["fragments"], [])
        self.assertEqual(oracao["text"], "Extensão não determinada")
        self.assertEqual(oracao["evidence_fragments"], ["quer"])
        self.assertEqual([t["id"] for t in oracao["tokens"]], ids(fonte, [1]))
        periodo = mapa["paragraphs"][0]["periods"][0]
        self.assertEqual([t["id"] for t in periodo["unassigned_tokens"]], ids(fonte, [0, 2, 3]))

    def test_arvore_respeita_paragrafos_periodos_e_ids_mesmo_com_textos_iguais(self):
        casos = (("repeticao", "Ela não saiu.\n\nEla não saiu.", [1, 1]),
                 ("tempo_relativo", "Ontem fiquei. Hoje fico.\n\nAmanhã ficarei.", [2, 1]))
        for nome, texto, tamanhos in casos:
            with self.subTest(nome=nome):
                fonte = construir_analise(nome, texto=texto)
                mapa = self.visualizar(fonte=fonte)
                segmentacao = fonte["anotacao"]["segmentacao"]
                self.assertEqual([len(p["periods"]) for p in mapa["paragraphs"]], tamanhos)
                ancoras = []
                for grupo, paragrafo in zip(mapa["paragraphs"], segmentacao["paragrafos"]):
                    self.assertEqual(grupo["paragraph"], paragrafo)
                    self.assertEqual([p["period"]["id"] for p in grupo["periods"]], paragrafo["periodos"])
                    for periodo in grupo["periods"]:
                        oracao, = periodo["clauses"]
                        self.assertEqual([t["id"] for t in oracao["tokens"]], periodo["period"]["tokens"])
                        self.assertEqual(periodo["unassigned_tokens"], [])
                        ancoras.append(oracao["anchor"])
                self.assertEqual(len(set(ancoras)), sum(tamanhos))

    def test_fragmentos_usam_original_mesmo_quando_crlf_foi_normalizado(self):
        fonte = construir_analise("desejo", texto="Ela\r\nquer sair.", normalizar=True)
        principal, subordinada = self.oracoes(self.visualizar(fonte=fonte))
        self.assertEqual(principal["fragments"], ["Ela\r\nquer", "."])
        self.assertEqual(subordinada["fragments"], ["sair"])

    def test_leitura_nao_muta_registros_e_nao_carrega_modelos(self):
        fonte = construir_analise("desejo")
        regras = aplicar_regras_linguisticas(fonte, execucao_id="mapa-imutavel", registrado_em=INSTANTE)
        anotacao = fonte["anotacao"]
        segmentacao = anotacao["segmentacao"]
        registros = (segmentacao, anotacao, fonte, regras)
        antes = json.dumps(registros, ensure_ascii=False, allow_nan=False)
        with patch("anotacao._carregar_modelo", side_effect=AssertionError("reanotação proibida")), \
             patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("reanálise proibida")), \
             patch("regras_linguisticas.aplicar_regras_linguisticas", side_effect=AssertionError("reexecução proibida")):
            mapa = construir_visualizacao_segmentacao(*registros)
        self.assertEqual(json.dumps(registros, ensure_ascii=False, allow_nan=False), antes)
        self.assertEqual(mapa["clauses_total"], 2)


if __name__ == "__main__":
    unittest.main()
