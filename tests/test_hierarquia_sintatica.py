"""Contratos do zoom sintático com árvores e dependências manuais independentes.

Nenhum teste requer Stanza ou pesos adicionais. As árvores controladas testam
alinhamento e navegação; não medem a qualidade de previsões de um modelo.
"""

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from fixtures_regras import construir_analise, ids, ponto, token, verbo
import hierarquia_sintatica as hierarquia
from hierarquia_sintatica import (
    ErroAlinhamento, ErroHierarquia, ErroLimite, ErroModelo,
    construir_hierarquia, validar_hierarquia,
)


INSTANTE = "2026-10-14T14:20:00-03:00"


def folha(texto):
    return {"texto": texto}


def no(rotulo, *filhos):
    return {"rotulo": rotulo, "filhos": list(filhos)}


class AdaptadorControlado:
    def __init__(self, arvore):
        self.arvore = arvore
        self.chamadas = []

    def parse(self, tokens):
        self.chamadas.append(list(tokens))
        return deepcopy(self.arvore)

    def provenance(self):
        return {"biblioteca": "teste-controlado", "versao": "1.0", "pacote": "arvore-manual",
                "sha256": "a" * 64, "disponivel": True}


class HierarquiaSintaticaTests(unittest.TestCase):
    def construir(self, nome="auxiliar", *, analise=None, arvore=None, periodo=0, **opcoes):
        analise = construir_analise(nome) if analise is None else analise
        segmento = analise["anotacao"]["segmentacao"]
        periodo_id = segmento["periodos"][periodo]["id"]
        token_ids = segmento["periodos"][periodo]["tokens"]
        tokens = [t for t in segmento["tokens"] if t["id"] in token_ids]
        if arvore is None:
            arvore = no("S", *(folha(t["texto"]) for t in tokens))
        adaptador = AdaptadorControlado(arvore)
        registro = construir_hierarquia(analise, periodo_id, adaptador=adaptador,
                                       hierarquia_id="hierarquia-teste", registrado_em=INSTANTE, **opcoes)
        return registro, adaptador

    def test_converte_arvore_controlada_em_estrutura_rastreavel_sem_mudar_origem(self):
        analise = construir_analise("auxiliar")
        snapshot = deepcopy(analise)
        arvore = no("S", no("NP", folha("Ela")),
                    no("VP", folha("está"), folha("estudando")), folha("."))
        registro, adaptador = self.construir(analise=analise, arvore=arvore)
        self.assertEqual(analise, snapshot)
        self.assertEqual(registro["analise"], snapshot)
        self.assertIsNot(registro["analise"], analise)
        self.assertEqual(adaptador.chamadas, [["Ela", "está", "estudando", "."]])
        self.assertEqual(registro["arvore"]["rotulo"], "S")
        self.assertEqual(registro["arvore"]["classificacao"], "Oração")
        sujeito, predicado, pontuacao = registro["arvore"]["filhos"]
        self.assertEqual(sujeito["classificacao"], "Sintagma nominal")
        self.assertEqual(sujeito["token_ids"], ids(analise, [0]))
        self.assertEqual(predicado["texto"], "está estudando")
        self.assertEqual(predicado["trabalho"], {"inicio": 4, "fim": 18})
        self.assertEqual(predicado["original"], {"inicio": 4, "fim": 18})
        self.assertEqual(predicado["nucleo_token_ids"], ids(analise, [2]))
        self.assertEqual(predicado["origem_papeis"], "candidatos_por_dependencias_v1")
        self.assertEqual(pontuacao["token_id"], ids(analise, [3])[0])
        self.assertEqual(registro["palavras"][0]["classe"], "Pronome")
        self.assertEqual(registro["palavras"][0]["funcao_sintatica"], "Núcleo do sujeito")
        self.assertEqual(validar_hierarquia(registro)["cobertura"], {"tokens": 4, "nos": 7, "oracoes": 1})
        self.assertEqual(registro, json.loads(json.dumps(registro, ensure_ascii=False, allow_nan=False)))

    def test_auxiliares_e_copulas_nao_criam_oracoes_adicionais(self):
        for nome in ("auxiliar", "copula"):
            with self.subTest(nome=nome):
                registro, _ = self.construir(nome)
                oracoes = registro["oracoes"]
                self.assertEqual(len(oracoes), 1)
                self.assertEqual(oracoes[0]["nucleo_token_ids"], ids(registro["analise"], [2]))
                self.assertEqual(oracoes[0]["grupo_verbal_token_ids"], ids(registro["analise"], [1, 2]))
                self.assertEqual(oracoes[0]["token_ids"], ids(registro["analise"], [0, 1, 2, 3]))
                if nome == "copula":
                    self.assertEqual(oracoes[0]["tipo_predicado"], "copular")
                else:
                    self.assertEqual(oracoes[0]["tipo_predicado"], "verbal")
                self.assertEqual(oracoes[0]["dependencia"], "ROOT")
                self.assertEqual(oracoes[0]["classificacoes_relacionais"], [])
                self.assertEqual(oracoes[0]["rotulo"], "Oração sem relação oracional identificada")

    def test_coordenadas_sao_irmas_com_relacao_explicita(self):
        registro, _ = self.construir("coordenacao")
        primeira, segunda = registro["oracoes"]
        self.assertIsNone(primeira["pai_oracao_id"])
        self.assertIsNone(segunda["pai_oracao_id"])
        self.assertEqual(segunda["relacoes"], [{"tipo": "coordenacao", "alvo_oracao_id": primeira["id"], "dependencia": "conj"}])
        self.assertEqual(segunda["conectivo_token_ids"], ids(registro["analise"], [2]))
        self.assertEqual(segunda["subtipo"], "Não determinado pelo método")
        for coordenada in (primeira, segunda):
            self.assertEqual(coordenada["classificacoes_relacionais"], ["coordenada"])
            self.assertEqual(coordenada["rotulo"], "Oração coordenada")
        self.assertEqual(primeira["dependencia"], "ROOT")
        self.assertEqual(segunda["dependencia"], "conj")
        self.assertEqual(primeira["token_ids"], ids(registro["analise"], [0, 1, 4]))
        self.assertEqual([t["texto"] for t in primeira["trechos"]], ["Ela estudou", "."])

    def test_relativa_embutida_e_principal_descontinua_preservam_arvore(self):
        linhas = [token("O", "o", "DET", 1, "det"), token("menino", "menino", "NOUN", 4, "nsubj"),
                  token("que", "que", "PRON", 3, "nsubj"), verbo("chegou", "chegar", 1, "acl:relcl"),
                  verbo("sorriu", "sorrir", 4), ponto(4)]
        analise = construir_analise(texto="O menino que chegou sorriu.", linhas=linhas)
        arvore = no("S", no("NP", folha("O"), folha("menino"),
                              no("SBAR", folha("que"), no("S", no("VP", folha("chegou"))))),
                    no("VP", folha("sorriu")), folha("."))
        registro, _ = self.construir(analise=analise, arvore=arvore)
        relativa, principal = registro["oracoes"]
        self.assertEqual(relativa["token_ids"], ids(analise, [2, 3]))
        self.assertEqual(relativa["pai_oracao_id"], principal["id"])
        self.assertEqual(relativa["relacoes"][0]["tipo"], "subordinacao")
        self.assertEqual(relativa["classificacoes_relacionais"], ["subordinada"])
        self.assertEqual(relativa["rotulo"], "Oração subordinada")
        self.assertEqual(principal["classificacoes_relacionais"], ["principal"])
        self.assertEqual(principal["rotulo"], "Oração principal")
        self.assertEqual(relativa["dependencia"], "acl:relcl")
        self.assertEqual(principal["token_ids"], ids(analise, [0, 1, 4, 5]))
        self.assertEqual([t["texto"] for t in principal["trechos"]], ["O menino", "sorriu."])
        sintagma = registro["arvore"]["filhos"][0]
        self.assertEqual(sintagma["token_ids"], ids(analise, [0, 1, 2, 3]))
        self.assertEqual(sintagma["modificador_token_ids"], ids(analise, [3]))
        self.assertEqual(sintagma["filhos"][2]["rotulo"], "SBAR")
        self.assertTrue(validar_hierarquia(registro)["pronto"])

    def test_coordenacao_dentro_de_subordinada_compartilha_pai_estrutural(self):
        linhas = [token("Ela", "ela", "PRON", 1, "nsubj"), verbo("disse", "dizer", 1),
                  token("que", "que", "SCONJ", 3, "mark"), verbo("chegou", "chegar", 1, "ccomp"),
                  token("e", "e", "CCONJ", 5, "cc"), verbo("saiu", "sair", 3, "conj"), ponto(1)]
        analise = construir_analise(texto="Ela disse que chegou e saiu.", linhas=linhas)
        registro, _ = self.construir(analise=analise)
        principal, primeira, segunda = registro["oracoes"]
        self.assertEqual(primeira["pai_oracao_id"], principal["id"])
        self.assertEqual(segunda["pai_oracao_id"], principal["id"])
        self.assertEqual(segunda["relacoes"][0]["alvo_oracao_id"], primeira["id"])
        self.assertEqual(primeira["conectivo_token_ids"], ids(analise, [2]))
        self.assertEqual(segunda["conectivo_token_ids"], ids(analise, [4]))
        self.assertEqual(principal["classificacoes_relacionais"], ["principal"])
        self.assertEqual(principal["rotulo"], "Oração principal")
        for coordenada in (primeira, segunda):
            self.assertEqual(coordenada["classificacoes_relacionais"], ["subordinada", "coordenada"])
            self.assertEqual(coordenada["rotulo"], "Oração subordinada e coordenada")
        self.assertTrue(validar_hierarquia(registro)["pronto"])

    def test_oracao_subordinada_pode_ser_principal_de_outra(self):
        linhas = [token("Ela", "ela", "PRON", 1, "nsubj"), verbo("disse", "dizer", 1),
                  token("que", "que", "SCONJ", 3, "mark"), verbo("pensou", "pensar", 1, "ccomp"),
                  token("que", "que", "SCONJ", 5, "mark"), verbo("sairia", "sair", 3, "ccomp"), ponto(1)]
        analise = construir_analise(texto="Ela disse que pensou que sairia.", linhas=linhas)
        registro, _ = self.construir(analise=analise)
        principal, intermediaria, ultima = registro["oracoes"]
        self.assertEqual(principal["classificacoes_relacionais"], ["principal"])
        self.assertEqual(intermediaria["classificacoes_relacionais"], ["principal", "subordinada"])
        self.assertEqual(intermediaria["rotulo"], "Oração principal e subordinada")
        self.assertEqual(ultima["classificacoes_relacionais"], ["subordinada"])
        self.assertEqual(intermediaria["pai_oracao_id"], principal["id"])
        self.assertEqual(ultima["pai_oracao_id"], intermediaria["id"])
        self.assertTrue(validar_hierarquia(registro)["pronto"])

    def test_complementos_e_cabecas_sao_candidatos_explicitos_por_dependencias(self):
        registro, _ = self.construir("desejo")
        analise = registro["analise"]
        self.assertEqual(registro["arvore"]["nucleo_token_ids"], ids(analise, [1]))
        self.assertEqual(registro["arvore"]["complemento_token_ids"], ids(analise, [2]))
        self.assertEqual(registro["oracoes"][1]["pai_oracao_id"], registro["oracoes"][0]["id"])

    def test_varias_cabecas_externas_nao_sao_inventadas_como_nucleo_unico(self):
        registro, _ = self.construir("coordenacao", arvore=no("S", no("NP", folha("Ela"), folha("estudou"), folha("e")),
                                                                 folha("passou"), folha(".")))
        self.assertEqual(registro["arvore"]["filhos"][0]["nucleo_token_ids"], ids(registro["analise"], [1, 2]))

    def test_periodo_nominal_e_rotulo_desconhecido_nao_recebem_classificacao_inventada(self):
        registro, _ = self.construir("nominal", arvore=no("CINTIL_DESCONHECIDO", folha("O"), folha("estudo"), folha(".")))
        self.assertEqual(registro["oracoes"], [])
        self.assertEqual(registro["arvore"]["rotulo"], "CINTIL_DESCONHECIDO")
        self.assertIn("não determinada", registro["arvore"]["classificacao"])
        self.assertTrue(any("Nenhum núcleo" in aviso for aviso in registro["avisos"]))
        self.assertNotIn("discurso", registro)
        self.assertTrue(validar_hierarquia(registro)["pronto"])

    def test_apenas_um_periodo_preservando_paragrafos_crlf_unicode_e_repeticoes(self):
        analise = construir_analise("unicode_crlf", normalizar=True)
        registro, adaptador = self.construir(analise=analise, periodo=1)
        self.assertEqual(adaptador.chamadas, [["Amanhã", "João", "e\u0301", "😀", "ficará", "."]])
        self.assertEqual(registro["paragrafo_id"], analise["anotacao"]["segmentacao"]["paragrafos"][1]["id"])
        self.assertNotEqual(registro["arvore"]["trabalho"], registro["arvore"]["original"])
        self.assertTrue(validar_hierarquia(registro)["pronto"])
        repetido, _ = self.construir("repeticao", periodo=1)
        self.assertEqual(repetido["arvore"]["token_ids"], ids(repetido["analise"], [4, 5, 6, 7]))

    def test_ler_validar_nao_carrega_modelo_nem_retokeniza(self):
        registro, _ = self.construir()
        snapshot = deepcopy(registro)
        with patch("hierarquia_sintatica._adaptador_padrao", side_effect=AssertionError("modelo executado")), \
             patch("anotacao._carregar_modelo", side_effect=AssertionError("morfologia executada")), \
             patch("sintaxe_entidades._carregar_modelo", side_effect=AssertionError("sintaxe executada")), \
             patch("segmentacao._tokenizar", side_effect=AssertionError("retokenizou")):
            self.assertTrue(validar_hierarquia(registro)["pronto"])
        self.assertEqual(registro, snapshot)

    def test_arvores_desalinhadas_vazias_ou_malformadas_sao_rejeitadas(self):
        arvores = [no("S", folha("Ela"), folha("estudando"), folha("está"), folha(".")),
                   no("S", folha("Ela"), folha("Ela"), folha("estudando"), folha(".")),
                   no("S", folha("Ela")), no("S"), folha("Ela"),
                   no("S", {"texto": "Ela", "rotulo": "N"}),
                   no("S", {"rotulo": "NP", "filhos": "Ela"}), no("S", {"texto": None})]
        for arvore in arvores:
            with self.subTest(arvore=arvore), self.assertRaises(ErroAlinhamento):
                self.construir(arvore=arvore)

    def test_arvore_ciclica_e_limites_sao_explicitos_sem_truncar(self):
        ciclica = no("S")
        ciclica["filhos"].append(ciclica)
        with self.assertRaises(ErroAlinhamento):
            self.construir(arvore=ciclica)
        profunda = no("S", folha("Ela"), folha("está"), folha("estudando"), folha("."))
        for _ in range(hierarquia.MAX_PROFUNDIDADE + 1):
            profunda = no("S", profunda)
        with self.assertRaises(ErroLimite):
            self.construir(arvore=profunda)
        with patch.object(hierarquia, "MAX_NOS", 3), self.assertRaises(ErroLimite):
            self.construir()
        with patch.object(hierarquia, "MAX_TOKENS_PERIODO", 3), self.assertRaisesRegex(ErroLimite, "nenhum conteúdo foi truncado"):
            self.construir()

    def test_origem_corrompida_e_periodo_estranho_rejeitados_antes_do_modelo(self):
        analise = construir_analise("auxiliar")
        adaptador = AdaptadorControlado(None)
        with self.assertRaises(ErroHierarquia):
            construir_hierarquia(analise, "periodo-ausente", adaptador=adaptador)
        analise["sintaxe"][0]["cabeca_token_id"] = "token-ausente"
        with self.assertRaises(ErroHierarquia):
            construir_hierarquia(analise, analise["anotacao"]["segmentacao"]["periodos"][0]["id"], adaptador=adaptador)
        self.assertEqual(adaptador.chamadas, [])

    def test_limite_real_de_150_tokens_por_periodo_preserva_a_origem(self):
        for quantidade in (149, 150):
            with self.subTest(tokens=quantidade + 1):
                linhas = [token("nome", "nome", "NOUN", 0, "ROOT" if i == 0 else "dep") for i in range(quantidade)]
                analise = construir_analise(texto=" ".join(["nome"] * quantidade) + ".", linhas=linhas + [ponto(0)])
                snapshot = deepcopy(analise)
                if quantidade == 149:
                    registro, _ = self.construir(analise=analise)
                    self.assertEqual(registro["validacao"]["cobertura"]["tokens"], 150)
                else:
                    with self.assertRaises(ErroLimite):
                        self.construir(analise=analise)
                self.assertEqual(analise, snapshot)

    def test_modelo_ausente_e_erro_nativo_de_alinhamento_traduzidos(self):
        analise = construir_analise("auxiliar")
        periodo = analise["anotacao"]["segmentacao"]["periodos"][0]["id"]
        with patch.object(AdaptadorControlado, "parse", side_effect=RuntimeError("modelo não instalado")):
            with self.assertRaises(ErroModelo):
                construir_hierarquia(analise, periodo, adaptador=AdaptadorControlado(None))
        from modelo_constituintes import AlinhamentoConstituintesError
        with patch.object(AdaptadorControlado, "parse", side_effect=AlinhamentoConstituintesError("folhas incompatíveis")):
            with self.assertRaises(ErroAlinhamento):
                construir_hierarquia(analise, periodo, adaptador=AdaptadorControlado(None))

    def test_registro_salvo_rejeita_corrupcao_de_arvore_origem_rotulos_e_derivados(self):
        registro, _ = self.construir()
        alteracoes = [
            lambda r: r.update(documento_id=True), lambda r: r.update(periodo_id="outro"),
            lambda r: r.update(paragrafo_id="outro"), lambda r: r["analise"]["sintaxe"][0].update(texto="alterado"),
            lambda r: r["arvore"].update(id="duplicado"), lambda r: r["arvore"].update(classificacao="Sintagma inventado"),
            lambda r: r["arvore"]["trabalho"].update(inicio=True),
            lambda r: r["arvore"]["original"].update(fim=999),
            lambda r: r["arvore"]["filhos"].reverse(),
            lambda r: r["arvore"]["filhos"].append(deepcopy(r["arvore"]["filhos"][0])),
            lambda r: r["arvore"]["filhos"][0].update(token_id="outro"),
            lambda r: r["arvore"].update(nucleo_token_ids=[]),
            lambda r: r["oracoes"][0].update(token_ids=[]), lambda r: r["oracoes"][0]["trechos"][0].update(texto="alterado"),
            lambda r: r["oracoes"][0].update(relacoes=[{"tipo": "subordinacao", "alvo_oracao_id": "ausente"}]),
            lambda r: r["oracoes"][0].update(classificacoes_relacionais=["principal"]),
            lambda r: r["oracoes"][0].update(rotulo="Oração absoluta"),
            lambda r: r["oracoes"][0].update(tipo_predicado="copular"),
            lambda r: r["oracoes"][0].update(dependencia="conj"),
            lambda r: r["palavras"][0].update(funcao_sintatica="inventada"),
            lambda r: r["processamento"].update(oracoes="outro_metodo"),
            lambda r: r["validacao"].update(pronto=1), lambda r: r.update(registrado_em="2026-10-14"),
            lambda r: r.update(avisos=[]), lambda r: r.update(campo_extra="invalido"),
        ]
        for numero, alterar in enumerate(alteracoes):
            with self.subTest(numero=numero):
                corrompido = deepcopy(registro)
                alterar(corrompido)
                with self.assertRaises(ErroHierarquia):
                    validar_hierarquia(corrompido)

    def test_json_estrito_rejeita_nan_objetos_e_ciclos(self):
        registro, _ = self.construir()
        for valor in (float("nan"), {1: "chave-não-textual"}, object()):
            with self.subTest(valor=type(valor).__name__):
                corrompido = deepcopy(registro)
                corrompido["processamento"]["constituencia"]["extra"] = valor
                with self.assertRaises(ErroHierarquia):
                    validar_hierarquia(corrompido)
        registro["arvore"]["filhos"].append(registro["arvore"])
        with self.assertRaises(ErroHierarquia):
            validar_hierarquia(registro)

    def test_identificadores_e_datas_opcionais_sao_validados(self):
        analise = construir_analise("auxiliar")
        periodo = analise["anotacao"]["segmentacao"]["periodos"][0]["id"]
        for opcoes in ({"hierarquia_id": " "}, {"hierarquia_id": 1}, {"registrado_em": "sem-data"},
                       {"registrado_em": "2026-10-14T14:20:00"}):
            with self.subTest(opcoes=opcoes), self.assertRaises(ErroHierarquia):
                construir_hierarquia(analise, periodo, adaptador=AdaptadorControlado(None), **opcoes)


if __name__ == "__main__":
    unittest.main()
