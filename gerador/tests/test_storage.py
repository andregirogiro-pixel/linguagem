"""Histórico, tentativas controladas e concorrência sem usar a API real."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from geracao_paragrafo import ErroGeracao
from gerador.app import storage
from gerador.app.validator import validar_paragrafo


CHAVE = "sk-or-v1-segredo-do-teste"
MODELO = "empresa/modelo-escolhido"
TEXTO = "Marina restaurou um mapa antigo na oficina. Depois levou a descoberta ao museu do bairro."
OUTRO_TEXTO = "Sob as nuvens escuras, Pedro recolheu as ferramentas do jardim. A chuva chegou quando ele fechava o portão."
VALIDO = {"aceito": True, "motivos": [], "metricas": {"frases": 2}}


def resposta(texto=TEXTO, **metadados):
    return {"texto": texto, "metadados": {"id": "resposta-simulada", **metadados}}


class HistoricoGeracaoTests(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)
        self.database = Path(self.pasta.name) / "lingo.sqlite3"
        # A presença da tabela principal comprova que o histórico não exige
        # outro banco nem recria as tabelas da aplicação.
        with sqlite3.connect(self.database) as conexao:
            conexao.execute("CREATE TABLE submissions (id INTEGER PRIMARY KEY, content TEXT)")
            conexao.execute("INSERT INTO submissions VALUES (1, 'Documento existente')")
        self.historico = storage.HistoricoGeracao(self.database)
        self.validacao_patch = patch.object(storage, "validar_paragrafo", return_value=VALIDO)
        self.validar = self.validacao_patch.start()
        self.addCleanup(self.validacao_patch.stop)

    def criar(self, quantidade=1):
        return self.historico.criar_lote(MODELO, {"tamanho": "curto"}, quantidade)

    def test_plano_exato_e_persistente_sem_api_ou_mudanca_no_banco_principal(self):
        with patch.object(storage, "gerar_paragrafo") as gerar:
            lote = self.criar(3)
        gerar.assert_not_called()
        self.assertEqual(lote["status"], "planejado")
        self.assertEqual(lote["concluidos"], 0)
        self.assertEqual([i["indice"] for i in lote["itens"]], [1, 2, 3])
        self.assertTrue(all(i["status"] == "pendente" and i["prompt"] is None for i in lote["itens"]))
        self.assertTrue(all(i["parametros"]["tamanho"] == "curto" for i in lote["itens"]))
        outro_processo = storage.HistoricoGeracao(self.database)
        self.assertEqual(outro_processo.obter_lote(lote["id"]), lote)
        with sqlite3.connect(self.database) as conexao:
            self.assertEqual(conexao.execute("SELECT content FROM submissions").fetchone()[0], "Documento existente")

    def test_prompt_e_reserva_persistem_antes_da_unica_chamada(self):
        lote = self.criar()

        def simular(provedor, modelo, chave, **opcoes):
            self.assertEqual((provedor, modelo, chave), ("openrouter", MODELO, CHAVE))
            self.assertTrue(opcoes["retorno_detalhado"])
            self.assertEqual(opcoes["temperatura"], lote["itens"][0]["temperatura"])
            em_andamento = storage.HistoricoGeracao(self.database).obter_lote(lote["id"])
            self.assertEqual(em_andamento["status"], "processando")
            self.assertEqual(em_andamento["itens"][0]["status"], "processando")
            self.assertEqual(em_andamento["itens"][0]["prompt"], opcoes["prompt"])
            self.assertIsNotNone(em_andamento["itens"][0]["iniciado_em"])
            return resposta(usage={"prompt_tokens": 20, "completion_tokens": 30})

        gerar = Mock(side_effect=simular)
        pronto = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        gerar.assert_called_once()
        self.assertEqual((pronto["status"], pronto["concluidos"], pronto["aceitos"]), ("concluido", 1, 1))
        item = pronto["itens"][0]
        self.assertEqual(item["texto"], TEXTO)
        self.assertIsNotNone(item["finalizado_em"])
        self.assertIn("duracao_ms", item["metadados"])
        self.assertEqual(item["metadados"]["usage"]["completion_tokens"], 30)
        self.assertTrue(item["metadados"]["diversidade"]["aceito"])
        self.assertEqual(self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar), pronto)
        gerar.assert_called_once()

    def test_rejeita_texto_repetido_no_lote_e_guarda_ambas_respostas(self):
        lote = self.criar(2)
        gerar = Mock(return_value=resposta())
        primeiro = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertEqual((primeiro["concluidos"], primeiro["status"]), (1, "processando"))
        segundo = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertEqual(gerar.call_count, 3)
        self.assertEqual((segundo["status"], segundo["concluidos"], segundo["aceitos"], segundo["rejeitados"]), ("parcial", 2, 1, 1))
        rejeitado = segundo["itens"][1]
        self.assertEqual(rejeitado["texto"], TEXTO)
        self.assertEqual(rejeitado["status"], "rejeitado")
        self.assertEqual(len(rejeitado["tentativas"]), 2)
        self.assertTrue(all(t["texto"] == TEXTO for t in rejeitado["tentativas"]))
        self.assertIn("texto_repetido", rejeitado["metadados"]["diversidade"]["motivos"])
        self.assertEqual(self.historico.textos_recentes(), [TEXTO])
        self.assertEqual(self.historico.exportar_lote(lote["id"]), segundo)

    def test_diversidade_inclui_historico_aceito_de_outros_lotes(self):
        primeiro = self.criar()
        self.historico.gerar_proximo(primeiro["id"], CHAVE, gerador=Mock(return_value=resposta()))
        segundo = self.criar()
        final = self.historico.gerar_proximo(segundo["id"], CHAVE, gerador=Mock(return_value=resposta()))
        self.assertEqual((final["status"], final["aceitos"]), ("falhou", 0))
        self.assertEqual(final["itens"][0]["status"], "rejeitado")

    def test_falha_na_api_grava_erro_sem_tentar_outro_item(self):
        lote = self.criar(2)
        gerar = Mock(side_effect=ErroGeracao("A chave de API foi recusada.", 401))
        falha = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        gerar.assert_called_once()
        self.assertEqual(falha["concluidos"], 1)
        self.assertEqual([i["status"] for i in falha["itens"]], ["erro", "pendente"])
        self.assertEqual(falha["itens"][0]["metadados"]["erro_status_code"], 401)
        gerar.return_value, gerar.side_effect = resposta(), None
        pronto = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertEqual(gerar.call_count, 2)
        self.assertEqual(pronto["status"], "parcial")
        self.assertEqual(pronto["erros"], 1)
        self.assertEqual(pronto["aceitos"], 1)

    def test_falha_unica_e_terminal_e_nao_expoe_detalhes_ou_chave(self):
        lote = self.criar()
        gerar = Mock(side_effect=RuntimeError(f"Authorization: Bearer {CHAVE}; corpo-privado"))
        final = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertEqual(final["status"], "falhou")
        self.assertEqual(final["itens"][0]["status"], "erro")
        serializado = json.dumps(final)
        self.assertNotIn(CHAVE, serializado)
        self.assertNotIn("corpo-privado", serializado)
        self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        gerar.assert_called_once()

    def test_chave_nunca_e_gravada_mesmo_se_cliente_a_incluir_na_resposta(self):
        lote = self.criar()
        gerar = Mock(return_value=resposta(
            nota=f"Não grave {CHAVE}",
            api_key=CHAVE,
            usage={"total_tokens": 23, "completion_tokens_details": {"reasoning_tokens": 7}},
        ))
        final = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertNotIn(CHAVE, json.dumps(self.historico.exportar_lote(lote["id"])))
        self.assertEqual(final["itens"][0]["metadados"]["usage"]["total_tokens"], 23)
        self.assertEqual(final["itens"][0]["metadados"]["usage"]["completion_tokens_details"]["reasoning_tokens"], 7)
        self.assertNotIn(CHAVE.encode(), self.database.read_bytes())

    def test_validacao_de_tamanho_rejeita_e_persiste_metricas_sem_avaliar_diversidade(self):
        self.validar.side_effect = validar_paragrafo
        lote = self.criar()
        with patch.object(storage, "avaliar_diversidade") as diversidade:
            final = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=Mock(return_value=resposta("A praça ficou vazia. O jardineiro recolheu as folhas. Uma criança acenou da janela. O sino tocou ao longe. A noite chegou devagar.")))
        diversidade.assert_not_called()
        self.assertEqual(final["itens"][0]["status"], "rejeitado")
        self.assertEqual(final["itens"][0]["metadados"]["validacao"]["metricas"]["frases"], 5)
        self.assertEqual(len(final["itens"][0]["tentativas"]), 2)
        self.assertEqual(self.historico.textos_recentes(), [])

    def test_temperatura_vem_do_yaml_jitter_e_preservado_apos_reiniciar(self):
        configuracao = {"temperatura": {"min": 0.0, "max": 1.2, "padrao": 0.8, "jitter_lote": 0.15}}
        with patch.object(storage, "carregar_configuracao", return_value=configuracao):
            simples = self.criar()
            self.assertEqual(simples["temperatura"], 0.8)
            self.assertEqual(simples["itens"][0]["temperatura"], 0.8)
            with patch.object(storage.random.SystemRandom, "uniform", side_effect=[-0.15, 0.0, 0.15]):
                lote = self.historico.criar_lote(MODELO, {}, 3, temperatura=1.2)
            self.assertEqual([i["temperatura"] for i in lote["itens"]], [1.05, 1.2, 1.2])
            reiniciado = storage.HistoricoGeracao(self.database)
            self.assertEqual(reiniciado.obter_lote(lote["id"]), lote)

    def test_temperatura_nao_numerica_ou_fora_da_faixa_nao_cria_lote(self):
        for temperatura in (True, -0.1, 1.21, "0.8", float("nan"), float("inf")):
            with self.subTest(temperatura=temperatura), self.assertRaises(storage.ErroHistorico):
                self.historico.criar_lote(MODELO, {}, 1, temperatura=temperatura)
        self.assertEqual(self.historico.listar_lotes(), [])

    def test_migracao_de_temperatura_preserva_lotes_e_tabelas_existentes(self):
        lote = self.criar()
        with sqlite3.connect(self.database) as conexao:
            conexao.execute("ALTER TABLE geracao_lotes DROP COLUMN temperatura")
            conexao.execute("ALTER TABLE geracao_itens DROP COLUMN temperatura")
        migrado = storage.HistoricoGeracao(self.database).obter_lote(lote["id"])
        self.assertEqual(migrado["itens"][0]["parametros"], lote["itens"][0]["parametros"])
        self.assertEqual(migrado["status"], "planejado")
        self.assertEqual(migrado["temperatura"], 0.8)
        self.assertEqual(migrado["itens"][0]["temperatura"], 0.8)
        with sqlite3.connect(self.database) as conexao:
            self.assertEqual(conexao.execute("SELECT COUNT(*) FROM submissions").fetchone()[0], 1)

    def test_valida_entradas_antes_de_reservar_ou_chamar_api(self):
        for modelo in (None, "", " ", True, "modelo com espaço", "modelo\n", "x" * 201):
            with self.subTest(modelo=modelo), self.assertRaises(storage.ErroHistorico):
                self.historico.criar_lote(modelo, {}, 1)
        for quantidade in (0, 21, True, None, 1.5, "2"):
            with self.subTest(quantidade=quantidade), self.assertRaises(storage.ErroHistorico):
                self.historico.criar_lote(MODELO, {}, quantidade)
        with self.assertRaises(storage.ErroHistorico):
            self.historico.criar_lote(MODELO, {"campo_inexistente": "valor"}, 1)
        self.assertEqual(self.historico.listar_lotes(), [])
        lote = self.criar()
        gerar = Mock()
        with self.assertRaises(storage.ErroHistorico):
            self.historico.gerar_proximo(lote["id"], "", gerador=gerar)
        gerar.assert_not_called()
        self.assertEqual(self.historico.obter_lote(lote["id"])["itens"][0]["status"], "pendente")

    def test_listagem_paginada_e_exportacao_incluem_dados_completos(self):
        lotes = [self.criar() for _ in range(3)]
        self.assertEqual([l["id"] for l in self.historico.listar_lotes(limit=1, offset=1)], [lotes[1]["id"]])
        exportado = self.historico.exportar_lote(lotes[0]["id"])
        for campo in ("modelo", "parametros_solicitados", "itens", "criado_em"):
            self.assertIn(campo, exportado)
        self.assertIn("+00:00", exportado["criado_em"])
        self.assertEqual(exportado["parametros_solicitados"], {"tamanho": "curto"})
        for opcoes in ({"limit": 101}, {"limit": 0}, {"offset": -1}, {"limit": True}):
            with self.subTest(opcoes=opcoes), self.assertRaises(storage.ErroHistorico):
                self.historico.listar_lotes(**opcoes)
        with self.assertRaises(storage.ErroHistorico) as capturado:
            self.historico.obter_lote(999999)
        self.assertEqual(capturado.exception.status_code, 404)

    def test_reserva_concorrente_do_mesmo_lote_chama_um_cliente_apenas(self):
        lote = self.criar(2)
        iniciou, liberar = threading.Event(), threading.Event()
        resultado, erros = [], []

        def lento(*args, **kwargs):
            iniciou.set()
            if not liberar.wait(5):
                raise RuntimeError("O teste não liberou a chamada")
            return resposta()

        def trabalhar():
            try:
                resultado.append(self.historico.gerar_proximo(lote["id"], CHAVE, gerador=Mock(side_effect=lento)))
            except Exception as erro:
                erros.append(erro)

        thread = threading.Thread(target=trabalhar, daemon=True)
        thread.start()
        try:
            self.assertTrue(iniciou.wait(5))
            outro_cliente = Mock()
            with self.assertRaises(storage.ErroHistorico) as capturado:
                storage.HistoricoGeracao(self.database).gerar_proximo(lote["id"], CHAVE, gerador=outro_cliente)
            self.assertEqual(capturado.exception.status_code, 409)
            outro_cliente.assert_not_called()
        finally:
            liberar.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(erros, [])
        self.assertEqual(resultado[0]["concluidos"], 1)

    def test_dois_lotes_simultaneos_nao_aprovam_o_mesmo_texto(self):
        primeiro, segundo = self.criar(), self.criar()
        iniciou, liberar = threading.Event(), threading.Event()
        resultado = []

        def lento(*args, **kwargs):
            iniciou.set()
            if not liberar.wait(5):
                raise RuntimeError("Teste não liberou a chamada")
            return resposta()

        thread = threading.Thread(target=lambda: resultado.append(
            self.historico.gerar_proximo(primeiro["id"], CHAVE, gerador=Mock(side_effect=lento))
        ), daemon=True)
        thread.start()
        try:
            self.assertTrue(iniciou.wait(5))
            rapido = self.historico.gerar_proximo(segundo["id"], CHAVE, gerador=Mock(return_value=resposta()))
            self.assertEqual(rapido["aceitos"], 1)
        finally:
            liberar.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(resultado[0]["rejeitados"], 1)
        self.assertEqual(self.historico.textos_recentes(), [TEXTO])

    def test_interrupcao_nao_reenvia_item_apos_reiniciar(self):
        lote = self.criar()
        gerar = Mock(side_effect=KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        reiniciado = storage.HistoricoGeracao(self.database)
        self.assertEqual(reiniciado.obter_lote(lote["id"])["itens"][0]["status"], "processando")
        with self.assertRaises(storage.ErroHistorico) as capturado:
            reiniciado.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertEqual(capturado.exception.status_code, 409)
        gerar.assert_called_once()

    def test_retry_de_conteudo_guarda_rejeitado_e_final_aceito(self):
        lote = self.criar()
        self.validar.side_effect = [
            {"aceito": False, "motivos": ["Texto incompleto"], "metricas": {}}, VALIDO,
        ]
        gerar = Mock(side_effect=[resposta("Texto inicial incompleto"), resposta()])
        final = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertEqual(gerar.call_count, 2)
        self.assertEqual(final["status"], "concluido")
        item = final["itens"][0]
        self.assertEqual([t["status"] for t in item["tentativas"]], ["rejeitado", "aceito"])
        self.assertEqual(item["tentativas"][0]["texto"], "Texto inicial incompleto")
        self.assertEqual(item["texto"], TEXTO)
        self.assertIn("Texto incompleto", item["tentativas"][1]["prompt"])
        self.assertNotEqual(item["tentativas"][0]["prompt"], item["tentativas"][1]["prompt"])
        self.assertTrue(final["metricas"])
        self.assertEqual(self.historico.exportar_lote(lote["id"])["itens"][0]["tentativas"], item["tentativas"])

    def test_limite_uma_tentativa_e_respeitado(self):
        lote = self.historico.criar_lote(MODELO, {}, 1, max_tentativas=1)
        self.validar.return_value = {"aceito": False, "motivos": ["Fora da faixa"], "metricas": {}}
        gerar = Mock(return_value=resposta())
        final = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        gerar.assert_called_once()
        self.assertEqual(final["status"], "falhou")
        self.assertEqual(len(final["itens"][0]["tentativas"]), 1)

    def test_erro_de_conteudo_do_parser_tambem_pode_tentar_de_novo(self):
        lote = self.criar()
        erro = ErroGeracao("O parágrafo foi truncado.", 502)
        erro.tipo = "conteudo"
        erro.texto_gerado = "Texto bruto sem final"
        erro.metadados = {"tentativas_rede": 1, "avisos": ["Resposta rejeitada"]}
        gerar = Mock(side_effect=[erro, resposta()])
        final = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertEqual(final["status"], "concluido")
        self.assertEqual(gerar.call_count, 2)
        item = final["itens"][0]
        self.assertEqual(item["tentativas"][0]["texto"], "Texto bruto sem final")
        self.assertEqual(item["tentativas"][0]["metadados"]["tentativas_rede"], 1)
        self.assertIn("Resposta rejeitada", item["avisos"])

    def test_controles_no_export_e_temperatura_nula_quando_nao_suportada(self):
        lote = self.historico.criar_lote(
            MODELO, {"tamanho": "curto"}, 3, tema="Um farol", anti_repeticao=False,
            n_historico=7, seed=42, modo_sorteio="uniforme", temperatura_suportada=False,
        )
        self.assertIsNone(lote["temperatura"])
        self.assertTrue(all(i["temperatura"] is None for i in lote["itens"]))
        self.assertGreater(len({i["parametros"]["ousadia"] for i in lote["itens"]}), 1)
        gerar = Mock(return_value=resposta())
        with patch.object(storage, "avaliar_diversidade") as diversidade:
            primeiro = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
            segundo = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        diversidade.assert_not_called()
        self.assertEqual(segundo["aceitos"], 2)
        self.assertIsNone(gerar.call_args.kwargs["temperatura"])
        self.assertIn("Um farol", primeiro["itens"][0]["prompt"])
        exportado = self.historico.exportar_lote(lote["id"])
        self.assertEqual(exportado["controles"]["seed"], 42)
        self.assertEqual(exportado["controles"]["modo_sorteio"], "uniforme")
        self.assertFalse(exportado["controles"]["anti_repeticao"])

    def test_seed_reproduz_plano_e_jitter_sem_alterar_ousadia_fixa(self):
        primeiro = self.historico.criar_lote(MODELO, {}, 3, seed=17)
        segundo = self.historico.criar_lote(MODELO, {}, 3, seed=17)
        self.assertEqual([i["parametros"] for i in primeiro["itens"]], [i["parametros"] for i in segundo["itens"]])
        self.assertEqual([i["temperatura"] for i in primeiro["itens"]], [i["temperatura"] for i in segundo["itens"]])
        catalogo = storage.carregar_configuracao()
        ousadia = catalogo["eixos"]["ousadia"]["valores"][0]
        fixo = self.historico.criar_lote(MODELO, {"ousadia": ousadia}, 3, temperatura_suportada=False)
        self.assertTrue(all(i["parametros"]["ousadia"] == ousadia for i in fixo["itens"]))

    def test_avaliacao_e_historico_filtrado_persistem_sem_mudar_validacao(self):
        primeiro, segundo = self.criar(), self.criar()
        final = self.historico.gerar_proximo(primeiro["id"], CHAVE, gerador=Mock(return_value=resposta()))
        item_id = final["itens"][0]["id"]
        self.assertEqual(self.historico.avaliar_item(item_id, True)["avaliacao"], "aprovado")
        self.assertEqual(storage.HistoricoGeracao(self.database).avaliar_item(item_id, False)["avaliacao"], "reprovado")
        item = self.historico.listar_geracoes(lote_id=primeiro["id"])[0]
        self.assertEqual(item["status"], "aceito")
        self.assertEqual(item["avaliacao"], "reprovado")
        self.assertEqual(item["lote_id"], primeiro["id"])
        self.assertEqual(self.historico.obter_item(item_id), item)
        self.assertEqual(self.historico.listar_geracoes(limite=1)[0]["lote_id"], segundo["id"])
        self.assertIsNone(self.historico.avaliar_item(item_id, None)["avaliacao"])
        with self.assertRaises(storage.ErroHistorico):
            self.historico.avaliar_item(item_id, "sim")

    def test_migracao_aditiva_preserva_textos_ids_e_prompts(self):
        lote = self.criar()
        final = self.historico.gerar_proximo(lote["id"], CHAVE, gerador=Mock(return_value=resposta()))
        with sqlite3.connect(self.database) as conexao:
            for coluna in ("controles_json", "metricas_json"):
                conexao.execute(f"ALTER TABLE geracao_lotes DROP COLUMN {coluna}")
            for coluna in ("avisos_json", "tentativas_json", "avaliacao"):
                conexao.execute(f"ALTER TABLE geracao_itens DROP COLUMN {coluna}")
        migrado = storage.HistoricoGeracao(self.database).obter_lote(lote["id"])
        self.assertEqual(migrado["itens"][0]["id"], final["itens"][0]["id"])
        self.assertEqual(migrado["itens"][0]["texto"], TEXTO)
        self.assertEqual(migrado["itens"][0]["prompt"], final["itens"][0]["prompt"])
        self.assertEqual(migrado["itens"][0]["tentativas"], [])
        self.assertIsNone(migrado["itens"][0]["avaliacao"])

    def test_interrupcao_na_segunda_tentativa_preserva_ambas_sem_terceira_cobranca(self):
        lote = self.criar()
        self.validar.return_value = {"aceito": False, "motivos": ["Precisa de correção"], "metricas": {}}
        gerar = Mock(side_effect=[resposta("Primeiro conteúdo rejeitado."), KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self.historico.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        reiniciado = storage.HistoricoGeracao(self.database)
        item = reiniciado.obter_lote(lote["id"])["itens"][0]
        self.assertEqual([t["status"] for t in item["tentativas"]], ["rejeitado", "processando"])
        self.assertEqual(item["tentativas"][0]["texto"], "Primeiro conteúdo rejeitado.")
        self.assertIsNotNone(item["tentativas"][1]["prompt"])
        with self.assertRaises(storage.ErroHistorico):
            reiniciado.gerar_proximo(lote["id"], CHAVE, gerador=gerar)
        self.assertEqual(gerar.call_count, 2)

    def test_controles_invalidos_nao_criam_plano(self):
        invalidos = (
            {"anti_repeticao": "sim"}, {"temperatura_suportada": 1},
            {"n_historico": 0}, {"n_historico": 201}, {"seed": True},
            {"max_tentativas": 0}, {"max_tentativas": 3},
            {"modo_sorteio": "inexistente"}, {"tema": ["tema"]},
        )
        for controles in invalidos:
            with self.subTest(controles=controles), self.assertRaises(storage.ErroHistorico):
                self.historico.criar_lote(MODELO, {}, 1, **controles)
        self.assertEqual(self.historico.listar_lotes(), [])


if __name__ == "__main__":
    unittest.main()
