"""Revisões verificáveis, limites de ambiguidade e preservação das previsões."""

from copy import deepcopy
import unittest
from unittest.mock import patch

import analise_gramatical as gramatica


LEXICO = {"cheguei": "chegar", "percebi": "perceber", "toquei": "tocar", "paguei": "pagar",
          "comecei": "começar", "respondi": "responder", "sei": "saber", "fui": "ser",
          "rei": "rei", "vi": "vir", "sorri": "sorrir", "parti": "partir", "dormi": "dormir"}


def palavra(texto, *, ident="t1", lema=None, pos="VERB", dependencia="ROOT", cabeca=None, morfologia=None):
    return {"token_id": ident, "texto": texto, "lema": texto if lema is None else lema,
            "pos": pos, "classe": {"VERB": "Verbo", "NOUN": "Substantivo", "ADV": "Advérbio"}.get(pos, pos),
            "morfologia": {} if morfologia is None else morfologia, "dependencia": dependencia,
            "funcao_sintatica": "Função bruta do modelo", "cabeca_token_id": ident if cabeca is None else cabeca,
            "trabalho": {"inicio": 0, "fim": len(texto)}, "original": {"inicio": 0, "fim": len(texto)}}


class RevisaoGramaticalTests(unittest.TestCase):
    def revisar(self, palavras):
        with patch.object(gramatica, "_carregar_lexico", return_value=(LEXICO, None)):
            return gramatica.revisar_palavras(palavras)

    def test_corrige_lema_e_flexao_atestados_sem_mudar_entrada_ou_observacoes(self):
        morf = {"VerbForm": "Fin", "Mood": "Ind", "Tense": "Pres", "Number": "Plur", "Person": "3"}
        fonte = [palavra("percebi", morfologia=morf)]
        snapshot = deepcopy(fonte)
        atual = self.revisar(fonte)[0]
        self.assertEqual(fonte, snapshot)
        for campo, valor in snapshot[0].items():
            self.assertEqual(atual[campo], valor)
        self.assertEqual(atual["revisao"]["lema"], "perceber")
        self.assertEqual(atual["revisao"]["morfologia"], {
            "VerbForm": "Fin", "Mood": "Ind", "Tense": "Past", "Number": "Sing", "Person": "1"})
        self.assertIn("'percebi'", atual["revisao"]["justificativas"][0])
        self.assertTrue(any("'Plur'" in j for j in atual["revisao"]["justificativas"]))
        atual["revisao"]["morfologia"]["Person"] = "2"
        self.assertEqual(morf["Person"], "3")

    def test_cobre_outros_verbos_e_alternancias_ortograficas_sem_frase_fixa(self):
        for forma, lema in (("cheguei", "chegar"), ("toquei", "tocar"), ("paguei", "pagar"),
                            ("comecei", "começar"), ("respondi", "responder")):
            with self.subTest(forma=forma):
                atual = self.revisar([palavra(forma)])[0]
                self.assertEqual(atual["revisao"]["lema"], lema)
                self.assertEqual(atual["revisao"]["morfologia"]["Tense"], "Past")

    def test_nao_forca_homografos_irregulares_nomes_ou_formas_em_ir(self):
        for forma in ("sei", "rei", "fui", "vi", "sorri", "parti", "dormi", "inventeiquei"):
            with self.subTest(forma=forma):
                atual = self.revisar([palavra(forma)])[0]
                self.assertNotIn("revisao", atual)
        atual = self.revisar([palavra("percebi", pos="NOUN")])[0]
        self.assertNotIn("revisao", atual)

    def test_nao_inventa_flexao_se_lema_do_lexico_nao_confere_com_forma(self):
        with patch.object(gramatica, "_carregar_lexico", return_value=({"cheguei": "perceber"}, None)):
            self.assertNotIn("revisao", gramatica.revisar_palavras([palavra("cheguei")])[0])

    def test_sujeito_explicito_conflitante_vira_alerta(self):
        sujeito = palavra("eles", ident="t0", pos="PRON", dependencia="nsubj", cabeca="t1",
                          morfologia={"Person": "3", "Number": "Plur"})
        atual = self.revisar([sujeito, palavra("percebi")])[1]
        self.assertNotIn("revisao", atual)
        self.assertIn("sujeito expresso diverge", atual["alertas_revisao"][0])

    def test_sujeito_coordenado_nao_recebe_primeira_pessoa_singular(self):
        fonte = [palavra("eu", ident="t0", pos="PRON", dependencia="nsubj", cabeca="t1",
                         morfologia={"Person": "1", "Number": "Sing"}),
                 palavra("percebi"), palavra("ela", ident="t2", pos="PRON", dependencia="conj", cabeca="t0")]
        atual = self.revisar(fonte)[1]
        self.assertNotIn("revisao", atual)
        self.assertTrue(atual["alertas_revisao"])

    def test_indica_ausencia_do_lexico_sem_apagar_dados_ou_impedir_revisao_sintatica(self):
        with patch.object(gramatica, "_carregar_lexico", return_value=({}, "Léxico indisponível.")):
            atual = gramatica.revisar_palavras([palavra("percebi", dependencia="ccomp")])[0]
        self.assertEqual(atual["revisao"]["lema"], "percebi")
        self.assertIn("Léxico indisponível.", atual["alertas_revisao"])
        self.assertEqual(atual["revisao"]["funcao_sintatica"], "Núcleo da oração completiva")
        self.assertNotIn("lexico", atual["revisao"])

    def test_funcao_da_palavra_descreve_nucleo_e_nao_oracao_inteira(self):
        for dependencia in ("advcl", "ccomp", "xcomp", "acl:relcl", "csubj"):
            with self.subTest(dependencia=dependencia):
                atual = self.revisar([palavra("chegou", dependencia=dependencia)])[0]
                self.assertTrue(atual["revisao"]["funcao_sintatica"].startswith("Núcleo da oração"))
                self.assertEqual(atual["dependencia"], dependencia)

    def test_quando_temporal_exige_mark_e_cabeca_de_oracao_adverbial(self):
        fonte = [palavra("Quando", ident="t0", pos="ADV", dependencia="mark", cabeca="t1"),
                 palavra("cheguei", dependencia="advcl", cabeca="t2"), palavra("percebi", ident="t2")]
        atual = self.revisar(fonte)[0]
        self.assertEqual(atual["pos"], "ADV")
        self.assertEqual(atual["revisao"]["pos"], "SCONJ")
        self.assertEqual(atual["revisao"]["classe"], "Conjunção subordinativa")
        self.assertEqual(atual["revisao"]["funcao_sintatica"], "Marcador de subordinação temporal")
        for dependencia, relacao_nucleo in (("advmod", "ROOT"), ("mark", "ccomp"), ("mark", "acl")):
            with self.subTest(dependencia=dependencia, relacao_nucleo=relacao_nucleo):
                fonte[0]["dependencia"] = dependencia
                fonte[1]["dependencia"] = relacao_nucleo
                self.assertNotIn("revisao", self.revisar(fonte)[0])

    def test_preposicao_com_verbo_de_movimento_nao_e_rotulada_objeto_direto(self):
        fonte = [palavra("cheguei"), palavra("à", ident="t2", pos="ADP", dependencia="case", cabeca="t3"),
                 palavra("biblioteca", ident="t3", pos="NOUN", dependencia="obj", cabeca="t1")]
        atual = self.revisar(fonte)[2]
        self.assertEqual(atual["dependencia"], "obj")
        self.assertEqual(atual["cabeca_token_id"], "t1")
        self.assertIn("termo preposicionado", atual["revisao"]["funcao_sintatica"])
        self.assertTrue(any("complemento locativo e adjunto" in j for j in atual["revisao"]["justificativas"]))
        # 'Cheguei a tempo' não pode virar uma afirmação categórica de destino.
        fonte[2]["texto"] = "tempo"
        self.assertNotIn("de destino", self.revisar(fonte)[2]["revisao"]["funcao_sintatica"])

    def test_objeto_sem_preposicao_ou_ligado_a_outro_verbo_fica_intacto(self):
        for lema, com_preposicao in (("chegar", False), ("ler", True)):
            fonte = [palavra("chegou", lema=lema), palavra("biblioteca", ident="t3", pos="NOUN", dependencia="obj", cabeca="t1")]
            if com_preposicao:
                fonte.append(palavra("à", ident="t2", pos="ADP", dependencia="case", cabeca="t3"))
            self.assertNotIn("revisao", self.revisar(fonte)[1])

    def test_grupo_em_ligado_ao_nome_apresenta_incerteza_sem_reanexar(self):
        fonte = [palavra("esquecido", lema="esquecer"),
                 palavra("caderno", ident="t2", pos="NOUN", dependencia="obj", cabeca="t1"),
                 palavra("em", ident="t3", pos="ADP", dependencia="case", cabeca="t4"),
                 palavra("casa", ident="t4", pos="NOUN", dependencia="nmod", cabeca="t2")]
        atual = self.revisar(fonte)[3]
        self.assertNotIn("revisao", atual)
        self.assertEqual(atual["cabeca_token_id"], "t2")
        self.assertIn("nenhuma ligação foi alterada", atual["alertas_revisao"][0])

    def test_metodo_e_licenca_do_lexico_ficam_rastreaveis(self):
        atual = self.revisar([palavra("percebi")])[0]["revisao"]
        self.assertEqual(atual["metodo"], gramatica.METODO)
        self.assertEqual(atual["lexico"], {"pacote": "spacy-lookups-data", "versao": "1.0.5",
                                          "tabela": "pt/lemma_lookup", "licenca": "MIT"})


class LexicoInstaladoTests(unittest.TestCase):
    def test_lexico_oficial_instalado_atesta_correcoes_reais(self):
        lexico, aviso = gramatica._carregar_lexico()
        if aviso:
            self.skipTest(aviso)
        self.assertEqual(lexico.get("cheguei"), "chegar")
        self.assertEqual(lexico.get("percebi"), "perceber")
        atual = gramatica.revisar_palavras([palavra("Cheguei")])[0]
        self.assertEqual(atual["revisao"]["lema"], "chegar")


if __name__ == "__main__":
    unittest.main()
