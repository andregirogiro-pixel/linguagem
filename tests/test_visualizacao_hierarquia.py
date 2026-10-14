"""Projeção por oração com gabaritos manuais, sem depender de modelos."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from fixtures_regras import construir_analise, ids, ponto, token, verbo
from hierarquia_sintatica import ErroHierarquia, construir_hierarquia
from test_hierarquia_sintatica import AdaptadorControlado, folha, no
from visualizacao_hierarquia import preparar_visualizacao


INSTANTE = "2026-10-14T14:20:00-03:00"


def construir(texto, linhas, arvore):
    analise = construir_analise(texto=texto, linhas=linhas)
    periodo_id = analise["anotacao"]["segmentacao"]["periodos"][0]["id"]
    return construir_hierarquia(analise, periodo_id, adaptador=AdaptadorControlado(arvore),
                               hierarquia_id="hierarquia-visual", registrado_em=INSTANTE)


def percorrer(raizes):
    for raiz in raizes:
        yield raiz
        yield from percorrer(raiz["filhos"])


def primeiro_periodo():
    # Inclui os erros de POS e lema da previsão anterior, que esta camada
    # registra, sem inventar uma correção ou alterar o histórico.
    linhas = [token("Quando", "quando", "ADV", 1, "advmod"),
              verbo("cheguei", "cheguei", 5, "advcl", tempo="Past"),
              token("à", "a", "ADP", 3, "case"),
              token("biblioteca", "biblioteca", "NOUN", 1, "obj"), ponto(5, ","),
              verbo("percebi", "percebi", 5), token("que", "que", "SCONJ", 8, "mark"),
              verbo("havia", "haver", 8, "aux", pos="AUX", tempo="Imp"),
              token("esquecido", "esquecer", "VERB", 5, "ccomp", {"VerbForm": "Part"}),
              token("o", "o", "DET", 10, "det"), token("caderno", "caderno", "NOUN", 8, "obj"),
              token("em", "em", "ADP", 12, "case"), token("casa", "casa", "NOUN", 8, "obl"), ponto(5)]
    biblioteca = no("PP", no("ADP", folha("à")), no("NP", no("NOUN", folha("biblioteca"))))
    temporal = no("CP", no("SCONJ", folha("Quando")),
                  no("S", no("VP", no("VERB", folha("cheguei")), biblioteca)))
    objeto = no("NP", no("DET", folha("o")), no("NOUN", folha("caderno")))
    local = no("PP", no("ADP", folha("em")), no("NP", no("NOUN", folha("casa"))))
    composta = no("VP", no("AUX", folha("havia")),
                  no("VP", no("VERB", folha("esquecido")), objeto, local))
    objetiva = no("CP", no("SCONJ", folha("que")), no("S", composta))
    arvore = no("ROOT", no("S", temporal, no("PUNCT", folha(",")),
                          no("VP", no("VERB", folha("percebi")), objetiva), no("PUNCT", folha("."))))
    return construir("Quando cheguei à biblioteca, percebi que havia esquecido o caderno em casa.", linhas, arvore)


class VisualizacaoHierarquiaTests(unittest.TestCase):
    def test_primeiro_periodo_mostra_oracoes_proprias_e_preserva_numeracao_original(self):
        registro = primeiro_periodo()
        vista = preparar_visualizacao(registro)
        temporal, principal, objetiva = vista["oracoes"]
        self.assertEqual([o["numero"] for o in vista["oracoes"]], [1, 2, 3])
        self.assertEqual([t["texto"] for t in temporal["trechos_leitura"]], ["Quando cheguei à biblioteca"])
        self.assertEqual([t["texto"] for t in principal["trechos_leitura"]], ["percebi"])
        self.assertEqual([t["texto"] for t in objetiva["trechos_leitura"]], ["que havia esquecido o caderno em casa"])
        self.assertEqual(principal["token_ids_leitura"], ids(registro["analise"], [5]))
        self.assertEqual([n["tipo"] for n in principal["raizes"]], ["palavra"])
        self.assertEqual(principal["raizes"][0]["texto"], "percebi")
        self.assertEqual(principal["raizes"][0]["rotulos_tecnicos"][0]["rotulo"], "VERB")
        self.assertTrue(principal["avisos"])
        for oracao in vista["oracoes"]:
            proprios = set(oracao["token_ids"])
            for node in percorrer(oracao["raizes"]):
                self.assertLessEqual(set(node["token_ids"]), proprios)
                self.assertNotEqual(node["rotulo"], "ROOT")
                self.assertNotIn("não determinada", node["classificacao"])

    def test_nos_e_intervalos_sao_referencias_completas_a_arvore_original(self):
        registro = primeiro_periodo()
        por_id = {n["id"]: n for n in percorrer([registro["arvore"]])}
        vista = preparar_visualizacao(registro)
        for oracao in vista["oracoes"]:
            for node in percorrer(oracao["raizes"]):
                original = por_id[node["id"]]
                for key in ("token_ids", "texto", "trabalho", "original", "rotulo", "tipo"):
                    self.assertEqual(node[key], original[key])
                for wrapper in node["rotulos_tecnicos"]:
                    self.assertEqual(wrapper, {k: por_id[wrapper["id"]][k] for k in ("id", "rotulo")})
            for trecho in oracao["trechos_leitura"]:
                texto = registro["analise"]["anotacao"]["segmentacao"]["preparacao"]["trabalho"]["texto"]
                self.assertEqual(texto[trecho["trabalho"]["inicio"]:trecho["trabalho"]["fim"]], trecho["texto"])

    def test_relativa_nao_vira_np_fabricado_apos_retirar_outra_oracao(self):
        linhas = [token("O", "o", "DET", 1, "det"), token("menino", "menino", "NOUN", 4, "nsubj"),
                  token("que", "que", "PRON", 3, "nsubj"), verbo("chegou", "chegar", 1, "acl:relcl"),
                  verbo("sorriu", "sorrir", 4), ponto(4)]
        relativa_bruta = no("CP", no("PRON", folha("que")), no("VP", no("VERB", folha("chegou"))))
        nominal = no("NP", no("DET", folha("O")), no("NOUN", folha("menino")), relativa_bruta)
        arvore = no("ROOT", no("S", nominal, no("VP", no("VERB", folha("sorriu"))), no("PUNCT", folha("."))))
        registro = construir("O menino que chegou sorriu.", linhas, arvore)
        vista = preparar_visualizacao(registro)
        relativa, principal = vista["oracoes"]
        self.assertEqual([t["texto"] for t in principal["trechos_leitura"]], ["O menino", "sorriu"])
        self.assertFalse(any(n["rotulo"] == "NP" for n in percorrer(principal["raizes"])))
        self.assertEqual([n["texto"] for n in principal["raizes"]], ["O", "menino", "sorriu"])
        self.assertTrue(principal["avisos"])
        self.assertEqual([t["texto"] for t in relativa["trechos_leitura"]], ["que chegou"])
        for oracao in vista["oracoes"]:
            exibidos = [n["token_id"] for n in percorrer(oracao["raizes"]) if n["tipo"] == "palavra"]
            lexicos = [ident for ident in oracao["token_ids"] if ident != ids(registro["analise"], [5])[0]]
            self.assertEqual(exibidos, lexicos)
        self.assertEqual(vista["arvore_tecnica"], registro["arvore"])

    def test_simplifica_wrappers_duplicados_e_pos_sem_colapsar_np_em_vp(self):
        linhas = [token("Estudar", "estudar", "VERB", 0, "ROOT", {"VerbForm": "Inf"}), ponto(0)]
        arvore = no("ROOT", no("S", no("NP", no("NP", no("VP", no("VERB", folha("Estudar")))))),
                    no("PUNCT", folha(".")))
        registro = construir("Estudar.", linhas, arvore)
        raiz = preparar_visualizacao(registro)["oracoes"][0]["raizes"][0]
        self.assertEqual(raiz["rotulo"], "NP")
        self.assertEqual(raiz["filhos"][0]["rotulo"], "VP")
        self.assertEqual(raiz["filhos"][0]["filhos"][0]["tipo"], "palavra")
        self.assertIn("NP", [n["rotulo"] for n in raiz["rotulos_tecnicos"]])
        self.assertEqual(len([n for n in percorrer([raiz]) if n["rotulo"] == "NP"]), 1)

    def test_classe_de_preterminal_divergente_e_preservada_sem_alterar_palavra(self):
        registro = primeiro_periodo()
        original = deepcopy(registro)
        vista = preparar_visualizacao(registro)
        palavra = vista["palavras"][0]
        self.assertEqual(palavra["pos"], "ADV")
        self.assertEqual(palavra["rotulos_pos_modelo"], ["SCONJ"])
        self.assertIn("dependências=ADV; constituintes=SCONJ", palavra["avisos"][0])
        self.assertEqual(registro, original)

    def test_leitura_de_registro_salvo_e_deterministica_sem_modelos(self):
        registro = primeiro_periodo()
        original = deepcopy(registro)
        with patch("hierarquia_sintatica._adaptador_padrao", side_effect=AssertionError("carregou modelo")), \
             patch("anotacao._carregar_modelo", side_effect=AssertionError("anotou")), \
             patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("sintaxe")):
            primeira = preparar_visualizacao(registro)
            segunda = preparar_visualizacao(registro)
        self.assertEqual(primeira, segunda)
        self.assertEqual(registro, original)
        self.assertEqual(primeira, json.loads(json.dumps(primeira, ensure_ascii=False)))
        primeira["arvore_tecnica"]["texto"] = "alterado na vista"
        primeira["palavras"][0]["lema"] = "alterado na vista"
        self.assertEqual(registro, original)

    def test_periodo_nominal_e_rotulos_nao_frasais_nao_recebem_classe_desconhecida(self):
        linhas = [token("O", "o", "DET", 1, "det"), token("estudo", "estudo", "NOUN", 1, "ROOT"), ponto(1)]
        arvore = no("ROOT", no("CONJP", no("NP", no("DET", folha("O")), no("NOUN", folha("estudo")))), no("PUNCT", folha(".")))
        vista = preparar_visualizacao(construir("O estudo.", linhas, arvore))
        self.assertEqual(vista["oracoes"], [])
        self.assertEqual(vista["raizes"][0]["rotulo"], "CONJP")
        self.assertEqual(vista["raizes"][0]["classificacao"], "Sintagma conjuntivo")
        self.assertFalse(any("não determinada" in n["classificacao"] for n in percorrer(vista["raizes"])))
        self.assertEqual([n["rotulo"] for n in percorrer(vista["raizes"])], ["CONJP", "NP", None, None])

    def test_pontuacao_interna_e_preservada_e_pontuacao_isolada_so_sai_da_leitura(self):
        linhas = [token("Ela", "ela", "PRON", 4, "nsubj"), ponto(4, ","),
                  token("hoje", "hoje", "ADV", 4, "advmod"), ponto(4, ","),
                  verbo("sorriu", "sorrir", 4), ponto(4)]
        arvore = no("S", *(folha(linha[0]) for linha in linhas))
        registro = construir("Ela, hoje, sorriu.", linhas, arvore)
        vista = preparar_visualizacao(registro)
        self.assertEqual([t["texto"] for t in vista["oracoes"][0]["trechos_leitura"]], ["Ela, hoje, sorriu"])
        self.assertEqual(registro["oracoes"][0]["trechos"][0]["texto"], "Ela, hoje, sorriu.")
        self.assertEqual(vista["arvore_tecnica"], registro["arvore"])

    def test_nao_oculta_palavra_apenas_por_pos_incorreto(self):
        linhas = [token("Não", "não", "PUNCT", 1, "advmod"), verbo("sorri", "sorrir", 1), ponto(1)]
        registro = construir("Não sorri.", linhas, no("S", folha("Não"), folha("sorri"), folha(".")))
        self.assertEqual(preparar_visualizacao(registro)["oracoes"][0]["trechos_leitura"][0]["texto"], "Não sorri")

    def test_rejeita_registro_corrompido_em_vez_de_apresentar_dados_incoerentes(self):
        registro = primeiro_periodo()
        registro["oracoes"][1]["token_ids"] = registro["oracoes"][0]["token_ids"]
        with self.assertRaises(ErroHierarquia):
            preparar_visualizacao(registro)


if __name__ == "__main__":
    unittest.main()
