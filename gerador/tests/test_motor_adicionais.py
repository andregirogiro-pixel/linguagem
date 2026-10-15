"""Casos do anexo: reparos, parâmetros de prompt e diversidade de lotes."""

import copy
import random
import unittest
from collections import Counter

from gerador.app.config import carregar_configuracao
from gerador.app.diversity import avaliar_diversidade, calcular_metricas
from gerador.app.prompt_builder import construir_prompt
from gerador.app.rules import aplicar_regras, compativel
from gerador.app.sampler import planejar_parametros, resolver_plano


def parametros_compativeis(config):
    parametros = {eixo: dados["valores"][0] for eixo, dados in config["eixos"].items()}
    parametros.update(
        tipo_texto="descricao", pessoa="terceira", tempo_verbal="presente",
        registro="formal", tom="neutro", publico="adulto", tamanho="medio",
        complexidade_sintatica="mista", vocabulario="medio",
        tipo_paragrafo="padrao", ousadia="equilibrada",
    )
    return parametros


CASOS_REGRAS = (
    ({"tipo_texto": "instrucao", "pessoa": "primeira"}, "pessoa", {"segunda", "impessoal"}),
    ({"tipo_texto": "relato", "pessoa": "impessoal"}, "pessoa", {"primeira"}),
    ({"tipo_texto": "relato", "tempo_verbal": "futuro"}, "tempo_verbal", {"passado"}),
    ({"registro": "tecnico", "tom": "bem_humorado"}, "tom", None),
    ({"publico": "crianca", "vocabulario": "rico"}, "vocabulario", {"simples"}),
    ({"publico": "crianca", "complexidade_sintatica": "complexa"}, "complexidade_sintatica", {"simples"}),
    ({"tipo_paragrafo": "transicao", "tamanho": "longo"}, "tamanho", {"curto"}),
    ({"tipo_paragrafo": "enfase", "tamanho": "medio"}, "tamanho", {"curto"}),
)


class RegrasAdicionaisTests(unittest.TestCase):
    def test_as_oito_incompatibilidades_fixas_sao_preservadas_com_aviso(self):
        config = carregar_configuracao()
        for incompatibilidade, alvo, _ in CASOS_REGRAS:
            parametros = parametros_compativeis(config)
            parametros.update(incompatibilidade)
            antes = copy.deepcopy(parametros)
            with self.subTest(combinacao=incompatibilidade, alvo=alvo):
                resultado = resolver_plano(parametros, 1, seed=42, configuracao=config)
                self.assertEqual(resultado["planos"], [antes])
                self.assertEqual(len(resultado["avisos_por_item"]), 1)
                self.assertTrue(resultado["avisos_por_item"][0])
                self.assertTrue(all(isinstance(aviso, str) for aviso in resultado["avisos_por_item"][0]))
                self.assertEqual(parametros, antes)

    def test_as_oito_regras_corrigem_alvo_sorteado_sem_mudar_os_fixos(self):
        for incompatibilidade, alvo, permitidos in CASOS_REGRAS:
            config = carregar_configuracao()
            parametros = parametros_compativeis(config)
            parametros.update(incompatibilidade)
            invalido = parametros[alvo]
            # Favorece fortemente a opção que exige reparo, sem tornar as
            # alternativas válidas indisponíveis para o novo sorteio.
            config["eixos"][alvo]["pesos"] = {
                valor: 1_000_000 if valor == invalido else 1
                for valor in config["eixos"][alvo]["valores"]
            }
            config_antes = copy.deepcopy(config)
            if permitidos is None:
                permitidos = set(config["eixos"][alvo]["valores"]) - {invalido}
            with self.subTest(combinacao=incompatibilidade, alvo=alvo):
                fixos = set(parametros) - {alvo}
                correcao = aplicar_regras(parametros, fixos, rng=random.Random(17), configuracao=config)
                plano = correcao["parametros"]
                self.assertIn(plano[alvo], permitidos)
                self.assertTrue(correcao["avisos"])
                for eixo, valor in parametros.items():
                    if eixo != alvo:
                        self.assertEqual(plano[eixo], valor)
                # O planejamento pode evitar a incompatibilidade antes de
                # aplicar um reparo, mas deve respeitar os mesmos fixos.
                pedido = {**parametros, alvo: "aleatorio"}
                planejado = resolver_plano(pedido, 1, seed=17, configuracao=config)["planos"][0]
                self.assertTrue(compativel(planejado, configuracao=config))
                self.assertTrue(all(planejado[eixo] == parametros[eixo] for eixo in fixos))
                self.assertEqual(config, config_antes)

    def test_variar_avisa_quando_regra_obriga_todos_os_tamanhos_a_curto(self):
        config = carregar_configuracao()
        parametros = parametros_compativeis(config)
        parametros.update(tipo_paragrafo="enfase", tamanho="variar")
        resultado = resolver_plano(parametros, 12, seed=9, configuracao=config)
        self.assertEqual([p["tamanho"] for p in resultado["planos"]], ["curto"] * 12)
        self.assertTrue(all(p["tipo_paragrafo"] == "enfase" for p in resultado["planos"]))
        self.assertTrue(any(resultado["avisos_por_item"]))

    def test_alvo_fixo_e_preservado_ao_corrigir_gatilho_sorteado(self):
        config = carregar_configuracao()
        parametros = parametros_compativeis(config)
        parametros.update(tipo_texto="instrucao", pessoa="primeira")
        fixos = set(parametros) - {"tipo_texto"}
        resultado = aplicar_regras(parametros, fixos, rng=random.Random(3), configuracao=config)
        self.assertEqual(resultado["parametros"]["pessoa"], "primeira")
        self.assertNotEqual(resultado["parametros"]["tipo_texto"], "instrucao")
        self.assertTrue(compativel(resultado["parametros"], configuracao=config))
        self.assertTrue(all(resultado["parametros"][eixo] == parametros[eixo] for eixo in fixos))
        self.assertTrue(resultado["avisos"])

    def test_ousadia_e_controles_de_diversidade_estao_no_catalogo_externo(self):
        config = carregar_configuracao()
        self.assertEqual(config["eixos"]["ousadia"]["camada"], 3)
        self.assertEqual(config["eixos"]["ousadia"]["valores"], ["conservadora", "equilibrada", "ousada"])
        self.assertEqual(config["frases_ousadia"]["equilibrada"], "")
        for chave, esperado in (
            ("anti_repeticao", True), ("n_historico", 50),
            ("abertura_palavras", 6), ("similaridade_ngramas", 3),
            ("limiar_similaridade", 0.4), ("max_tentativas", 2),
        ):
            with self.subTest(chave=chave):
                self.assertEqual(config[chave], esperado)


class SorteioAdicionalTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "eixos": {"cenario": {"valores": ["lunar", "submarino"], "pesos": {"lunar": 1, "submarino": 0}}}
        }

    def test_uniforme_permite_valores_de_peso_zero_e_ponderado_respeita_os_pesos(self):
        ponderado = resolver_plano({}, 20, seed=21, modo_sorteio="ponderado", configuracao=self.config)
        uniforme = resolver_plano({}, 20, seed=21, modo_sorteio="uniforme", configuracao=self.config)
        self.assertEqual({p["cenario"] for p in ponderado["planos"]}, {"lunar"})
        self.assertEqual({p["cenario"] for p in uniforme["planos"]}, {"lunar", "submarino"})

    def test_semente_reproduz_parametros_e_avisos_em_ambos_os_modos(self):
        for modo in ("uniforme", "ponderado"):
            with self.subTest(modo=modo):
                primeiro = resolver_plano({}, 20, seed=98, modo_sorteio=modo, configuracao=self.config)
                segundo = resolver_plano({}, 20, seed=98, modo_sorteio=modo, configuracao=self.config)
                self.assertEqual(primeiro, segundo)

    def test_variar_ignora_pesos_e_mantem_ciclos_balanceados(self):
        resultado = resolver_plano({"cenario": "variar"}, 20, seed=4, configuracao=self.config)
        valores = [p["cenario"] for p in resultado["planos"]]
        self.assertEqual(Counter(valores), {"lunar": 10, "submarino": 10})
        for inicio in range(0, 20, 2):
            self.assertEqual(set(valores[inicio:inicio + 2]), {"lunar", "submarino"})
        self.assertTrue(all(not avisos for avisos in resultado["avisos_por_item"]))

    def test_wrapper_legado_mantem_lista_e_aceita_modo_uniforme(self):
        planos = planejar_parametros({}, 20, random.Random(5), modo_sorteio="uniforme", configuracao=self.config)
        self.assertIsInstance(planos, list)
        self.assertEqual({p["cenario"] for p in planos}, {"lunar", "submarino"})


class PromptAdicionalTests(unittest.TestCase):
    def setUp(self):
        self.config = carregar_configuracao()
        self.parametros = parametros_compativeis(self.config)

    def test_tres_redacoes_distintas_preservam_tema_e_parametros(self):
        prompts = [
            construir_prompt(self.parametros, [], tema="Uma oficina de relógios na Lua", template_indice=indice, configuracao=self.config)
            for indice in range(3)
        ]
        self.assertEqual(len(set(prompts)), 3)
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                self.assertIn("Uma oficina de relógios na Lua", prompt)
                self.assertIn("parágrafo", prompt)
                for eixo, valor in self.parametros.items():
                    self.assertIn(f"[{eixo}]", prompt)
                    self.assertIn(f"[{valor}]", prompt)
                self.assertIn("entre 4 e 6 frases", prompt)

    def test_tema_vazio_solicita_escolha_livre_e_ousadia_usa_frase_do_yaml(self):
        self.config["frases_ousadia"]["conservadora"] = "Mantenha uma abordagem sóbria de teste."
        self.config["frases_ousadia"]["ousada"] = "Explore associações inesperadas de teste."
        for ousadia in ("conservadora", "equilibrada", "ousada"):
            self.parametros["ousadia"] = ousadia
            prompt = construir_prompt(self.parametros, [], configuracao=self.config)
            with self.subTest(ousadia=ousadia):
                self.assertIn("livremente", prompt)
                for valor in ("conservadora", "ousada"):
                    frase = self.config["frases_ousadia"][valor]
                    if valor == ousadia:
                        self.assertIn(frase, prompt)
                    else:
                        self.assertNotIn(frase, prompt)

    def test_historico_do_prompt_respeita_janela_e_chave_anti_repeticao(self):
        historico = [
            "Zebras amarelas cruzaram pontes antigas silenciosamente.",
            "Foguetes azuis atravessaram mares distantes lentamente.",
        ]
        prompt = construir_prompt(self.parametros, historico, n_historico=1, configuracao=self.config)
        self.assertNotIn("zebras amarelas", prompt.lower())
        self.assertIn("foguetes azuis atravessaram mares distantes lentamente", prompt.lower())
        sem_historico = construir_prompt(self.parametros, historico, anti_repeticao=False, configuracao=self.config)
        self.assertNotIn("zebras amarelas", sem_historico.lower())
        self.assertNotIn("foguetes azuis", sem_historico.lower())


class DiversidadeAdicionalTests(unittest.TestCase):
    def test_trigramas_diferenciam_ordem_mesmo_com_vocabulario_identico(self):
        resultado = avaliar_diversidade(
            "Alfa beta gama delta epsilon zeta.",
            ["Zeta epsilon delta gama beta alfa."],
        )
        self.assertTrue(resultado["aceito"])
        self.assertEqual(resultado["metricas"]["maior_jaccard"], 0)

    def test_jaccard_de_trigramas_usa_limiar_estritamente_maior(self):
        texto = "Alfa beta gama delta epsilon zeta."
        anteriores = ["Alfa beta gama delta epsilon eta."]
        no_limiar = avaliar_diversidade(texto, anteriores, limiar_similaridade=0.6)
        acima = avaliar_diversidade(texto, anteriores, limiar_similaridade=0.59)
        self.assertAlmostEqual(no_limiar["metricas"]["maior_jaccard"], 0.6)
        self.assertTrue(no_limiar["aceito"])
        self.assertFalse(acima["aceito"])
        self.assertIn("vocabulario_semelhante", acima["motivos"])

    def test_tamanho_de_ngrama_configuravel_altera_similaridade(self):
        texto = "Alfa beta gama delta epsilon zeta."
        historico = ["Alfa gama beta delta epsilon zeta."]
        trigramas = avaliar_diversidade(texto, historico, similaridade_ngramas=3, limiar_similaridade=0.2)
        bigramas = avaliar_diversidade(texto, historico, similaridade_ngramas=2, limiar_similaridade=0.2)
        self.assertTrue(trigramas["aceito"])
        self.assertAlmostEqual(trigramas["metricas"]["maior_jaccard"], 1 / 7, places=6)
        self.assertFalse(bigramas["aceito"])
        self.assertAlmostEqual(bigramas["metricas"]["maior_jaccard"], 0.25)

    def test_abertura_padrao_tem_seis_palavras_e_pode_ser_configurada(self):
        texto = "Na calma manhã de domingo Ana avistou cometas radiantes."
        anteriores = ["Na calma manhã de outono Beatriz recolheu tecidos coloridos."]
        self.assertTrue(avaliar_diversidade(texto, anteriores)["aceito"])
        quatro = avaliar_diversidade(texto, anteriores, abertura_palavras=4)
        self.assertFalse(quatro["aceito"])
        self.assertIn("abertura_repetida", quatro["motivos"])
        seis_iguais = avaliar_diversidade(texto, ["Na calma manhã de domingo Ana desenhou castelos dourados."])
        self.assertIn("abertura_repetida", seis_iguais["motivos"])

    def test_janela_avalia_somente_os_ultimos_itens_e_pode_ser_desligada(self):
        texto = "Astronautas descobriram cristais luminosos sob dunas geladas."
        historico = [texto, "Uma costureira consertava bolsas antigas junto da janela."]
        janela = avaliar_diversidade(texto, historico, n_historico=1)
        self.assertTrue(janela["aceito"])
        self.assertEqual(janela["metricas"]["paragrafos_comparados"], 1)
        self.assertFalse(avaliar_diversidade(texto, historico, n_historico=2)["aceito"])
        desligada = avaliar_diversidade(texto, historico, anti_repeticao=False)
        self.assertTrue(desligada["aceito"])
        self.assertEqual(desligada["motivos"], [])

    def test_metricas_agregam_palavras_comprimento_e_parametros_sem_mutacao(self):
        textos = ["Sol aquece jardins.", "Sol ilumina rios distantes."]
        planos = [{"tom": "leve", "variante": "pt_br"}, {"tom": "serio", "variante": "pt_br"}]
        planos_antes = copy.deepcopy(planos)
        metricas = calcular_metricas(textos, planos)
        self.assertEqual(metricas["proporcao_aberturas_unicas"], 1)
        self.assertAlmostEqual(metricas["diversidade_lexical"], 6 / 7, places=6)
        self.assertAlmostEqual(metricas["comprimento"]["media"], 3.5)
        self.assertAlmostEqual(metricas["comprimento"]["desvio"], 0.5)
        self.assertEqual(metricas["distribuicao_parametros"], {"tom": {"leve": 1, "serio": 1}, "variante": {"pt_br": 2}})
        self.assertEqual(planos, planos_antes)

    def test_metricas_de_lote_vazio_nao_dividem_por_zero(self):
        metricas = calcular_metricas([], [])
        self.assertEqual(metricas["proporcao_aberturas_unicas"], 0)
        self.assertEqual(metricas["diversidade_lexical"], 0)
        self.assertEqual(metricas["comprimento"], {"media": 0, "desvio": 0})
        self.assertEqual(metricas["distribuicao_parametros"], {})

    def test_metricas_de_aberturas_respeitam_quantidade_de_palavras_configurada(self):
        textos = [
            "Na calma manhã de domingo Ana correu.",
            "Na calma manhã de outono Beatriz caminhou.",
        ]
        planos = [{"tom": "leve"}, {"tom": "leve"}]
        self.assertEqual(calcular_metricas(textos, planos)["proporcao_aberturas_unicas"], 1)
        config = carregar_configuracao()
        config["abertura_palavras"] = 4
        metricas = calcular_metricas(textos, planos, configuracao=config)
        self.assertEqual(metricas["proporcao_aberturas_unicas"], 0.5)


if __name__ == "__main__":
    unittest.main()
