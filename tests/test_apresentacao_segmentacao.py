"""Contratos da leitura em painéis com previsões e árvores controladas."""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from apresentacao_segmentacao import preparar_paineis, resumo_segmentacao
from fixtures_regras import construir_analise, ponto, token, verbo
from preparacao import preparar_texto
from revisao_hierarquia import preparar_revisao
from segmentacao import segmentar_preparacao
from test_hierarquia_sintatica import AdaptadorControlado, folha, no
from test_visualizacao_hierarquia import construir, primeiro_periodo
from hierarquia_sintatica import construir_hierarquia


PRIMEIRO_PERIODO = "Quando cheguei à biblioteca, percebi que havia esquecido o caderno em casa."
PRIMEIRO_PARAGRAFO = (
    PRIMEIRO_PERIODO + " A bibliotecária, que já me conhecia, ofereceu algumas folhas e indicou uma mesa perto da janela. "
    "Eu queria estudar para a prova, mas o barulho da rua dificultava minha concentração. "
    "Como precisava terminar a leitura naquela manhã, procurei um lugar mais silencioso."
)


def paineis(registro):
    with patch("analise_gramatical._carregar_lexico", return_value=({"cheguei": "chegar", "percebi": "perceber"}, None)):
        revisada = preparar_revisao(registro)
        return preparar_paineis(registro, revisada)


def registro_contagem(segmentacao, periodo, numero):
    return {"hierarquia_id": "hierarquia:" + periodo["id"], "periodo_id": periodo["id"],
            "analise": {"anotacao": {"segmentacao": deepcopy(segmentacao)}},
            "oracoes": [{"id": str(i)} for i in range(numero)]}


class ApresentacaoPaineisTests(unittest.TestCase):
    def test_periodo_mostra_pp_inteiro_e_grupo_verbal_sem_forcar_regencia(self):
        registro = primeiro_periodo()
        vista = paineis(registro)
        temporal, principal, objetiva = vista["oracoes"]
        biblioteca = next(t for t in temporal["termos"] if t["texto"] == "à biblioteca")
        self.assertEqual(biblioteca["tipo"], "Sintagma preposicional")
        self.assertEqual(biblioteca["funcao"], "Expressão preposicionada ligada ao verbo")
        self.assertNotIn("objeto direto", biblioteca["funcao"].lower())
        self.assertIn("complemento e adjunto", biblioteca["observacao"])
        self.assertIn("percebi", [t["texto"] for t in principal["termos"]])
        verbais = [t for t in objetiva["termos"] if "verbal" in t["funcao"]]
        self.assertEqual([t["texto"] for t in verbais], ["havia esquecido"])
        self.assertIn("o caderno", [t["texto"] for t in objetiva["termos"]])
        self.assertEqual(sum(t["texto"] == "caderno" for t in objetiva["termos"]), 0)

    def test_sujeito_inferido_apenas_com_evidencia_e_fora_do_texto(self):
        vista = paineis(primeiro_periodo())
        for oracao in vista["oracoes"][:2]:
            sujeito = next(t for t in oracao["termos"] if t["inferido"])
            self.assertEqual(sujeito["texto"], "(eu)")
            self.assertEqual(sujeito["token_ids"], [])
            self.assertIn("não integra o texto", sujeito["observacao"])
            self.assertNotIn("(eu)", [p["texto"] for p in oracao["morfologia"]])
        self.assertFalse(any(t["inferido"] for t in vista["oracoes"][2]["termos"]))

    def test_nao_infere_por_numero_singular_por_terceira_pessoa_ou_sem_finitude(self):
        for atributos in ({"VerbForm": "Fin", "Number": "Sing"},
                          {"VerbForm": "Fin", "Person": "3", "Number": "Sing"},
                          {"VerbForm": "Inf", "Person": "1", "Number": "Sing"}):
            with self.subTest(atributos=atributos):
                linhas = [token("Sair", "sair", "VERB", 0, "ROOT", atributos), ponto(0)]
                registro = construir("Sair.", linhas, no("S", folha("Sair"), folha(".")))
                self.assertFalse(any(t["inferido"] for t in paineis(registro)["oracoes"][0]["termos"]))

    def test_pessoa_primeira_do_modelo_nao_resolve_formas_ambiguas(self):
        for forma, lema, tempo in (("Disse", "dizer", "Past"), ("Havia", "haver", "Imp")):
            with self.subTest(forma=forma):
                linhas = [token(forma, lema, "VERB", 0, "ROOT", {
                    "VerbForm": "Fin", "Mood": "Ind", "Person": "1", "Number": "Sing", "Tense": tempo}), ponto(0)]
                registro = construir(forma + ".", linhas, no("S", folha(forma), folha(".")))
                with patch("analise_gramatical._carregar_lexico", return_value=({forma.casefold(): lema}, None)):
                    vista = preparar_paineis(registro, preparar_revisao(registro))
                self.assertFalse(any(t["inferido"] for t in vista["oracoes"][0]["termos"]))

    def test_flexao_regular_ja_correta_confirma_sujeito_sem_exigir_correcao(self):
        linhas = [token("Falei", "falar", "VERB", 0, "ROOT", {
            "VerbForm": "Fin", "Mood": "Ind", "Person": "1", "Number": "Sing", "Tense": "Past"}), ponto(0)]
        registro = construir("Falei.", linhas, no("S", folha("Falei"), folha(".")))
        with patch("analise_gramatical._carregar_lexico", return_value=({"falei": "falar"}, None)):
            vista = preparar_paineis(registro, preparar_revisao(registro))
        self.assertNotIn("revisao", vista["palavras"][0])
        self.assertEqual([t["texto"] for t in vista["oracoes"][0]["termos"] if t["inferido"]], ["(eu)"])
        with patch("analise_gramatical._carregar_lexico", return_value=({}, "Léxico indisponível")):
            sem_lexico = preparar_paineis(registro, preparar_revisao(registro))
        self.assertFalse(any(t["inferido"] for t in sem_lexico["oracoes"][0]["termos"]))

    def test_sujeito_explicito_impede_inferencia_mesmo_com_flexao_primeira_pessoa(self):
        linhas = [token("Eu", "eu", "PRON", 1, "nsubj"),
                  token("estudo", "estudar", "VERB", 1, "ROOT", {
                      "VerbForm": "Fin", "Person": "1", "Number": "Sing", "Tense": "Pres", "Mood": "Ind"}), ponto(1)]
        registro = construir("Eu estudo.", linhas, no("S", no("NP", folha("Eu")), no("VP", folha("estudo")), folha(".")))
        termos = paineis(registro)["oracoes"][0]["termos"]
        self.assertFalse(any(t["inferido"] for t in termos))
        sujeito = next(t for t in termos if t["texto"] == "Eu")
        self.assertEqual(sujeito["tipo"], "Sintagma nominal")
        self.assertEqual(sujeito["funcao"], "Sujeito")

    def test_copula_nao_vira_predicado_verbal(self):
        analise = construir_analise("copula")
        periodo_id = analise["anotacao"]["segmentacao"]["periodos"][0]["id"]
        registro = construir_hierarquia(analise, periodo_id, adaptador=AdaptadorControlado(
            no("S", no("NP", folha("Ela")), no("VP", folha("está"), folha("feliz")), folha("."))))
        termos = paineis(registro)["oracoes"][0]["termos"]
        predicativo = next(t for t in termos if t["texto"] == "está feliz")
        self.assertEqual(predicativo["tipo"], "Predicação copular")
        self.assertEqual(predicativo["funcao"], "Núcleo predicativo")
        self.assertFalse(any("predicado verbal" in t["funcao"].lower() for t in termos))

    def test_periodo_nominal_tem_morfologia_propria_sem_pontuacao_ou_oracao_fabricada(self):
        analise = construir_analise("nominal")
        periodo_id = analise["anotacao"]["segmentacao"]["periodos"][0]["id"]
        registro = construir_hierarquia(analise, periodo_id, adaptador=AdaptadorControlado(
            no("S", no("NP", folha("O"), folha("estudo")), folha("."))))
        antes = deepcopy(registro)
        vista = paineis(registro)
        self.assertEqual(vista["oracoes"], [])
        self.assertEqual([linha["texto"] for linha in vista["morfologia_periodo"]], ["O", "estudo"])
        self.assertEqual([linha["classe"] for linha in vista["morfologia_periodo"]], ["Determinante", "Substantivo"])
        self.assertEqual(vista["arvore_tecnica"], registro["arvore"])
        self.assertEqual(registro, antes)

    def test_morfologia_independente_usa_revisao_sem_pontuacao_ou_transitividade(self):
        vista = paineis(primeiro_periodo())
        linhas = [p for o in vista["oracoes"] for p in o["morfologia"]]
        self.assertEqual(len(linhas), 12)
        self.assertFalse(any(p["texto"] in {",", "."} for p in linhas))
        percebi = next(p for p in linhas if p["texto"] == "percebi")
        self.assertEqual(percebi["lema"], "perceber")
        self.assertIn("primeira pessoa", percebi["resumo"])
        self.assertIn("pretérito perfeito", percebi["resumo"])
        havia = next(p for p in linhas if p["texto"] == "havia")
        self.assertIn("imperfeito", havia["resumo"])
        self.assertNotIn("imperativo", havia["resumo"])
        contracao = next(p for p in linhas if p["texto"] == "à")
        self.assertEqual(contracao["composicao"], "preposição a + artigo definido a")
        self.assertNotIn("intransitivo", json.dumps(linhas, ensure_ascii=False))

    def test_composicao_crase_depende_de_evidencia_adp_case(self):
        registro = primeiro_periodo()
        with patch("analise_gramatical._carregar_lexico", return_value=({}, None)):
            revisada = preparar_revisao(registro)
        palavra = next(p for p in revisada["palavras"] if p["texto"] == "à")
        for campo, valor in (("pos", "ADV"), ("dependencia", "advmod")):
            alterada = deepcopy(revisada)
            next(p for p in alterada["palavras"] if p["texto"] == "à")[campo] = valor
            linhas = [p for o in preparar_paineis(registro, alterada)["oracoes"] for p in o["morfologia"]]
            self.assertNotIn("composicao", next(p for p in linhas if p["texto"] == "à"))
        self.assertEqual(palavra["pos"], "ADP")

    def test_arvore_com_relativa_nao_vira_sintagma_fabricado(self):
        linhas = [token("O", "o", "DET", 1, "det"), token("menino", "menino", "NOUN", 4, "nsubj"),
                  token("que", "que", "PRON", 3, "nsubj"), verbo("chegou", "chegar", 1, "acl:relcl"),
                  verbo("sorriu", "sorrir", 4), ponto(4)]
        arvore = no("S", no("NP", folha("O"), folha("menino"), no("CP", folha("que"), folha("chegou"))),
                    no("VP", folha("sorriu")), folha("."))
        vista = paineis(construir("O menino que chegou sorriu.", linhas, arvore))
        principal = next(o for o in vista["oracoes"] if o["pai_oracao_id"] is None)
        sujeito = next(t for t in principal["termos"] if t["funcao"] == "Núcleo do sujeito")
        self.assertEqual(sujeito["texto"], "menino")
        self.assertNotEqual(sujeito["tipo"], "Sintagma nominal")
        self.assertFalse(any(t["texto"] == "O menino" for t in principal["termos"]))

    def test_numeracao_segue_primeiro_trecho_proprio_sem_alterar_ids_relacoes_ou_raw(self):
        linhas = [token("O", "o", "DET", 1, "det"), token("homem", "homem", "NOUN", 4, "nsubj"),
                  token("que", "que", "PRON", 3, "nsubj"), verbo("chegou", "chegar", 1, "acl:relcl"),
                  verbo("saiu", "sair", 4), ponto(4)]
        arvore = no("S", no("NP", folha("O"), folha("homem"), no("CP", folha("que"), folha("chegou"))),
                    no("VP", folha("saiu")), folha("."))
        registro = construir("O homem que chegou saiu.", linhas, arvore)
        antes = deepcopy(registro)
        vista = paineis(registro)
        principal, relativa = vista["oracoes"]
        self.assertEqual(principal["numero"], 1)
        self.assertEqual(relativa["numero"], 2)
        self.assertEqual(principal["id"], registro["oracoes"][1]["id"])
        self.assertEqual(relativa["id"], registro["oracoes"][0]["id"])
        self.assertIsNone(principal["pai_oracao_id"])
        self.assertEqual(relativa["pai_oracao_id"], principal["id"])
        self.assertEqual(relativa["relacoes"], registro["oracoes"][0]["relacoes"])
        self.assertEqual([t["texto"] for t in principal["trechos_leitura"]], ["O homem", "saiu"])
        self.assertEqual(registro, antes)

    def test_entrada_e_revisao_preservadas_e_saida_serializavel(self):
        registro = primeiro_periodo()
        revisada = preparar_revisao(registro)
        antes, antes_revisao = deepcopy(registro), deepcopy(revisada)
        with patch("hierarquia_sintatica._adaptador_padrao", side_effect=AssertionError("executou modelo")):
            vista = preparar_paineis(registro, revisada)
        self.assertEqual(registro, antes)
        self.assertEqual(revisada, antes_revisao)
        self.assertEqual(vista["arvore_tecnica"], registro["arvore"])
        self.assertEqual(vista, json.loads(json.dumps(vista, ensure_ascii=False)))
        vista["oracoes"][0]["termos"][0]["texto"] = "mudança na vista"
        self.assertEqual(registro, antes)
        self.assertEqual(revisada, antes_revisao)


class ResumoSegmentacaoTests(unittest.TestCase):
    def segmentar(self, texto=PRIMEIRO_PARAGRAFO):
        return segmentar_preparacao(preparar_texto(texto, documento_id="estatisticas"))

    def test_contagens_literais_do_exemplo_sem_total_oracional_prematuro(self):
        segmentacao = self.segmentar()
        antes = deepcopy(segmentacao)
        periodo = segmentacao["periodos"][0]
        registro = registro_contagem(segmentacao, periodo, 3)
        resumo = resumo_segmentacao(segmentacao, {periodo["id"]: registro})
        self.assertEqual(resumo["periodos"][periodo["id"]], {"palavras": 12, "caracteres": 75, "oracoes": 3})
        paragrafo = resumo["paragrafos"][segmentacao["paragrafos"][0]["id"]]
        self.assertEqual(paragrafo["palavras"], 54)
        self.assertEqual(paragrafo["caracteres"], 341)
        self.assertEqual(paragrafo["periodos"], 4)
        self.assertIsNone(paragrafo["oracoes"])
        self.assertEqual(paragrafo["oracoes_identificadas"], 3)
        self.assertEqual(paragrafo["periodos_analisados"], 1)
        self.assertFalse(paragrafo["completo"])
        self.assertEqual(segmentacao, antes)

    def test_todas_as_execucoes_necessarias_para_total_incluindo_periodo_nominal_zero(self):
        segmentacao = self.segmentar("Ela estudou. O estudo.")
        primeiro, segundo = segmentacao["periodos"]
        incompleto = resumo_segmentacao(segmentacao, [registro_contagem(segmentacao, primeiro, 1)])
        paragrafo_id = segmentacao["paragrafos"][0]["id"]
        self.assertIsNone(incompleto["periodos"][segundo["id"]]["oracoes"])
        self.assertIsNone(incompleto["paragrafos"][paragrafo_id]["oracoes"])
        completo = resumo_segmentacao(segmentacao, [registro_contagem(segmentacao, primeiro, 1),
                                                   registro_contagem(segmentacao, segundo, 0)])
        self.assertEqual(completo["periodos"][segundo["id"]]["oracoes"], 0)
        self.assertEqual(completo["paragrafos"][paragrafo_id]["oracoes"], 1)
        self.assertTrue(completo["paragrafos"][paragrafo_id]["completo"])

    def test_destaques_reconstroem_espacos_quebras_pontuacao_e_unicode(self):
        texto = "“Olá!”\t Ela chegou.\nE saiu?\n\nÀ noite, dizê-lo é bom. 😀 123!"
        segmentacao = self.segmentar(texto)
        resumo = resumo_segmentacao(segmentacao, {})
        for paragrafo in segmentacao["paragrafos"]:
            dados = resumo["paragrafos"][paragrafo["id"]]
            self.assertEqual("".join(t["texto"] for t in dados["trechos"]), paragrafo["texto"])
            for trecho in dados["trechos"]:
                if "periodo_id" in trecho:
                    periodo = next(p for p in segmentacao["periodos"] if p["id"] == trecho["periodo_id"])
                    self.assertEqual(trecho["texto"], periodo["texto"])
            self.assertEqual(dados["caracteres"], len(paragrafo["texto"]))

    def test_palavras_escritas_conservam_contracoes_compostos_e_cliticos(self):
        segmentacao = self.segmentar("À noite, guarda-chuva e dizê-lo. — 😀 123!")
        dados = resumo_segmentacao(segmentacao, {})["paragrafos"][segmentacao["paragrafos"][0]["id"]]
        self.assertEqual(dados["palavras"], 6)

    def test_execucao_de_outra_segmentacao_nao_completa_historico_atual(self):
        segmentacao = self.segmentar(PRIMEIRO_PERIODO)
        outra = self.segmentar(PRIMEIRO_PERIODO)
        registro = registro_contagem(outra, segmentacao["periodos"][0], 99)
        dados = resumo_segmentacao(segmentacao, [registro])
        self.assertIsNone(dados["periodos"][segmentacao["periodos"][0]["id"]]["oracoes"])
        self.assertFalse(dados["paragrafos"][segmentacao["paragrafos"][0]["id"]]["completo"])

    def test_registro_e_envelope_aceitos_sem_modificacao(self):
        segmentacao = self.segmentar(PRIMEIRO_PERIODO)
        registro = registro_contagem(segmentacao, segmentacao["periodos"][0], 3)
        original = deepcopy(registro)
        simples = resumo_segmentacao(segmentacao, registro)
        for entrada in ([{"hierarquia": registro, "visualizacao": {}}],
                        {"hierarquia": registro, "visualizacao": {}}):
            self.assertEqual(simples, resumo_segmentacao(segmentacao, entrada))
        self.assertEqual(registro, original)


if __name__ == "__main__":
    unittest.main()
