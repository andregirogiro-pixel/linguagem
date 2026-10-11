"""Gerar tudo conserva a cadeia 03–08 e deixa a inferência vetorial à escolha.

Somente os dois backends de previsão linguística são substituídos por
anotações manuais. Construção, validação, transações e downloads são reais.
"""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from werkzeug.datastructures import MultiDict

import app as app_module
from app import create_app
from anotacao import ErroAnotacao, validar_anotacao
from fixtures_contexto import LINHAS, TEXTO
from fixtures_regras import construir_analise
from preparacao import ErroPreparacao, validar_registro
from regras_linguisticas import ErroRegras, validar_regras_linguisticas
from segmentacao import ErroSegmentacao, validar_segmentacao
from sintaxe_entidades import ErroAnalise, validar_analise_sintaxe_entidades
from unidades_contexto import ErroContexto, validar_unidades_contexto


class GeracaoCompletaTests(unittest.TestCase):
    TABELAS = ("submissions", "preparations", "segmentations", "annotations",
               "analyses", "rule_runs", "context_runs")
    TABELAS_VETORES = ("vector_jobs", "vector_job_events", "vector_blobs", "vector_runs",
                      "vector_representations", "vector_cache")
    ETAPAS = (
        ("segmentacao", "save_segmentation", ErroSegmentacao),
        ("morfologia", "save_annotation", ErroAnotacao),
        ("sintaxe", "save_analysis", ErroAnalise),
        ("regras", "save_rule_run", ErroRegras),
        ("contexto", "save_context_run", ErroContexto),
    )
    SELETORES = ("preparacao_id", "segmentacao_id", "anotacao_id", "analise_id",
                 "execucao_id", "contexto_execucao_id")

    @classmethod
    def setUpClass(cls):
        cls.previsao = construir_analise(texto=TEXTO, linhas=LINHAS)

    def setUp(self):
        temporario = tempfile.TemporaryDirectory()
        self.addCleanup(temporario.cleanup)
        self.database = Path(temporario.name) / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database), "VECTORS_AUTO_START": False}
        self.client = create_app(self.config).test_client()
        for nome, backend in (("anotacao._executar", self.morfologia_manual),
                              ("sintaxe_entidades._executar", self.sintaxe_manual)):
            substituto = patch(nome, side_effect=backend)
            substituto.start()
            self.addCleanup(substituto.stop)

    def morfologia_manual(self, segmentacao, texto):
        self.assertEqual(texto, segmentacao["preparacao"]["trabalho"]["texto"])
        anotacoes = []
        for fonte, referencia in zip(segmentacao["tokens"], self.previsao["anotacao"]["anotacoes_tokens"]):
            self.assertEqual(fonte["texto"], referencia["texto"])
            item = deepcopy(referencia)
            item.update(token_id=fonte["id"], **{campo: deepcopy(fonte[campo]) for campo in
                        ("texto", "periodo_id", "paragrafo_id", "trabalho", "original")})
            anotacoes.append(item)
        processamento = deepcopy(self.previsao["anotacao"]["processamento"])
        processamento["configuracoes"]["tokenizador"]["versao_origem"] = segmentacao["processamento"]["versao"]
        return anotacoes, processamento

    def sintaxe_manual(self, anotacao, texto):
        segmentacao = anotacao["segmentacao"]
        self.assertEqual(texto, segmentacao["preparacao"]["trabalho"]["texto"])
        antigos = self.previsao["anotacao"]["segmentacao"]["tokens"]
        ids = {antigo["id"]: atual["id"] for antigo, atual in zip(antigos, segmentacao["tokens"])}
        sintaxe = []
        for fonte, referencia in zip(segmentacao["tokens"], self.previsao["sintaxe"]):
            item = deepcopy(referencia)
            item.update(token_id=fonte["id"], cabeca_token_id=ids[item["cabeca_token_id"]],
                        **{campo: deepcopy(fonte[campo]) for campo in
                           ("texto", "periodo_id", "paragrafo_id", "trabalho", "original")})
            sintaxe.append(item)
        processamento = deepcopy(self.previsao["processamento"])
        processamento["configuracoes"]["tokenizador"]["versao_origem"] = segmentacao["processamento"]["versao"]
        return sintaxe, [], processamento

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return {tabela: connection.execute(f"SELECT * FROM {tabela} ORDER BY rowid").fetchall()
                    for tabela in self.TABELAS + self.TABELAS_VETORES}

    def enviar(self, *, normalizar=False):
        dados = {"content": TEXTO}
        if normalizar:
            dados["normalizar_crlf"] = "on"
        resposta = self.client.post("/envios/completo", data=dados)
        self.assertEqual(resposta.status_code, 303, resposta.get_data(as_text=True))
        local = urlsplit(resposta.headers["Location"])
        return resposta, int(local.path.rsplit("/", 1)[1]), parse_qs(local.query)

    def cadeia(self, documento_id):
        registros = {}
        validadores = (validar_registro, validar_segmentacao, validar_anotacao,
                       validar_analise_sintaxe_entidades, validar_regras_linguisticas,
                       validar_unidades_contexto)
        with sqlite3.connect(self.database) as connection:
            documento = connection.execute("SELECT content,created_at FROM submissions WHERE id=?", (documento_id,)).fetchone()
            self.assertEqual(documento[0], TEXTO)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            anterior = None
            for tabela, validar, campo in zip(self.TABELAS[1:], validadores,
                    (None, "preparacao", "segmentacao", "anotacao", "analise", "regras")):
                linhas = connection.execute(f"SELECT record_json FROM {tabela} WHERE submission_id=?", (documento_id,)).fetchall()
                self.assertLessEqual(len(linhas), 1)
                if not linhas:
                    break
                registro = json.loads(linhas[0][0])
                validar(registro)
                self.assertEqual(registro["documento_id"], documento_id)
                if campo is not None:
                    self.assertEqual(registro[campo], anterior)
                else:
                    self.assertEqual(registro["original"]["texto"], TEXTO)
                    self.assertEqual(registro["metadados_origem"], {
                        "documento_id": documento_id, "recebido_em": documento[1],
                    })
                registros[tabela] = registro
                anterior = registro
        return registros

    def test_gerar_tudo_preserva_fontes_crlf_e_conclui_contexto_sem_gerar_vetores(self):
        with patch("app.enfileirar_vetorizacao") as enfileirar, patch("app.iniciar_trabalho") as iniciar:
            resposta, documento_id, selecao = self.enviar(normalizar=True)
        enfileirar.assert_not_called()
        iniciar.assert_not_called()
        self.assertEqual(set(selecao), set(self.SELETORES))
        self.assertEqual(urlsplit(resposta.headers["Location"]).fragment, "etapa-vetorizacao")
        armazenados = self.cadeia(documento_id)
        self.assertEqual(set(armazenados), set(self.TABELAS[1:]))
        contexto = armazenados["context_runs"]
        self.assertEqual(selecao["contexto_execucao_id"], [contexto["execucao_id"]])
        self.assertEqual(selecao["execucao_id"], [armazenados["rule_runs"]["execucao_id"]])
        for tabela, campo in zip(self.TABELAS[1:5], self.SELETORES[:4]):
            self.assertEqual(selecao[campo], [armazenados[tabela][campo]])
        self.assertEqual(armazenados["preparations"]["trabalho"]["texto"], TEXTO.replace("\r\n", "\n"))
        self.assertTrue(validar_unidades_contexto(contexto)["pronto_para_etapa_09"])
        download = self.client.get(f"/envios/{documento_id}/contexto.json", query_string={
            "contexto_execucao_id": contexto["execucao_id"],
        })
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.get_json(), contexto)
        antes = self.snapshot()
        for tabela in self.TABELAS:
            self.assertEqual(len(antes[tabela]), 1)
        for tabela in self.TABELAS_VETORES:
            self.assertEqual(antes[tabela], [])
        for client in (self.client, create_app(self.config).test_client()):
            self.assertEqual(client.get(resposta.headers["Location"]).status_code, 200)
            self.assertEqual(client.get(resposta.headers["Location"]).status_code, 200)
        self.assertEqual(self.snapshot(), antes)

    def test_gerar_tudo_tambem_preserva_crlf_quando_normalizacao_nao_foi_escolhida(self):
        _, documento_id, _ = self.enviar()
        armazenados = self.cadeia(documento_id)
        self.assertEqual(armazenados["preparations"]["trabalho"]["texto"], TEXTO)
        self.assertEqual(len(armazenados["context_runs"]["unidades"]), 5)

    def test_falha_de_etapa_reverte_sua_insercao_e_conserva_etapas_ja_concluidas(self):
        for indice, (etapa, nome, erro) in enumerate(self.ETAPAS):
            with self.subTest(etapa=etapa):
                antes = self.snapshot()
                salvar = getattr(app_module, nome)

                def inserir_e_falhar(*args, _salvar=salvar, _erro=erro, **kwargs):
                    _salvar(*args, **kwargs)
                    raise _erro("Falha controlada depois da inserção.")

                with patch("app." + nome, side_effect=inserir_e_falhar):
                    resposta, documento_id, selecao = self.enviar(normalizar=True)
                self.assertEqual(selecao["geracao_etapa"], [etapa])
                self.assertEqual(selecao["geracao_erro"], ["Falha controlada depois da inserção."])
                self.assertEqual(urlsplit(resposta.headers["Location"]).fragment, "etapa-" + etapa)
                self.assertEqual(set(selecao) - {"geracao_etapa", "geracao_erro"}, set(self.SELETORES[:indice + 1]))
                depois = self.snapshot()
                for numero, tabela in enumerate(self.TABELAS):
                    self.assertEqual(len(depois[tabela]) - len(antes[tabela]), int(numero <= indice + 1))
                armazenados = self.cadeia(documento_id)
                self.assertEqual(set(armazenados), set(self.TABELAS[1:indice + 2]))
                for client in (self.client, create_app(self.config).test_client()):
                    pagina = client.get(resposta.headers["Location"])
                    self.assertEqual(pagina.status_code, 200, pagina.get_data(as_text=True))
                    self.assertIn("Falha controlada depois da inserção.", pagina.get_data(as_text=True))
                    self.assertEqual(client.get(resposta.headers["Location"]).status_code, 200)
                self.assertEqual(self.snapshot(), depois)

    def test_regras_incompletas_sao_salvas_e_impedem_contexto(self):
        with patch("regras_linguisticas.detectar_regra", side_effect=RuntimeError("Falha controlada no detector.")):
            resposta, documento_id, selecao = self.enviar()
        self.assertEqual(selecao["geracao_etapa"], ["regras"])
        self.assertEqual(urlsplit(resposta.headers["Location"]).fragment, "etapa-regras")
        armazenados = self.cadeia(documento_id)
        self.assertEqual(set(armazenados), set(self.TABELAS[1:-1]))
        regras = armazenados["rule_runs"]
        self.assertFalse(validar_regras_linguisticas(regras)["pronto_para_etapa_08"])
        self.assertEqual(selecao["execucao_id"], [regras["execucao_id"]])
        self.assertNotIn("contexto_execucao_id", selecao)
        antes = self.snapshot()
        self.assertEqual(antes["context_runs"], [])
        for tabela in self.TABELAS_VETORES:
            self.assertEqual(antes[tabela], [])
        self.assertEqual(self.client.get(resposta.headers["Location"]).status_code, 200)
        self.assertEqual(self.snapshot(), antes)

    def test_falha_do_banco_reverte_etapa_atual_e_permite_consultar_as_anteriores(self):
        salvar = app_module.save_annotation

        def inserir_e_falhar(*args, **kwargs):
            salvar(*args, **kwargs)
            raise sqlite3.OperationalError("Falha de banco controlada.")

        with patch("app.save_annotation", side_effect=inserir_e_falhar):
            resposta, documento_id, selecao = self.enviar()
        self.assertEqual(selecao["geracao_etapa"], ["morfologia"])
        self.assertEqual(urlsplit(resposta.headers["Location"]).fragment, "etapa-morfologia")
        self.assertEqual(set(self.cadeia(documento_id)), {"preparations", "segmentations"})
        pagina = create_app(self.config).test_client().get(resposta.headers["Location"])
        self.assertEqual(pagina.status_code, 200)
        self.assertIn("As etapas concluídas continuam disponíveis.", pagina.get_data(as_text=True))
        self.assertNotIn("Falha de banco controlada.", pagina.get_data(as_text=True))
        for tabela in self.TABELAS_VETORES:
            self.assertEqual(self.snapshot()[tabela], [])

    def test_erro_de_preparacao_reverte_texto_e_preparacao_inseridos(self):
        antes = self.snapshot()
        salvar = app_module.save_preparation

        def inserir_e_falhar(*args, **kwargs):
            salvar(*args, **kwargs)
            raise ErroPreparacao("Falha controlada de preparação.")

        with patch("app.save_preparation", side_effect=inserir_e_falhar):
            resposta = self.client.post("/envios/completo", data={"content": TEXTO})
        self.assertEqual(resposta.status_code, 400)
        self.assertIn("Falha controlada de preparação.", resposta.get_data(as_text=True))
        self.assertEqual(self.snapshot(), antes)

    def test_formulario_estrito_e_texto_vazio_nao_criam_registros(self):
        antes = self.snapshot()
        invalidos = ({}, {"content": " \t\r\n"}, {"content": TEXTO, "acao": "completo"},
                     {"content": TEXTO, "normalizar_crlf": "true"},
                     MultiDict([("content", TEXTO), ("content", TEXTO)]),
                     MultiDict([("content", TEXTO), ("normalizar_crlf", "on"), ("normalizar_crlf", "on")]))
        for dados in invalidos:
            with self.subTest(dados=dados):
                resposta = self.client.post("/envios/completo", data=dados)
                self.assertEqual(resposta.status_code, 400, resposta.get_data(as_text=True))
                self.assertEqual(self.snapshot(), antes)
        self.assertEqual(self.client.post("/envios/completo", json={"content": TEXTO}).status_code, 400)
        self.assertEqual(self.client.post("/envios/completo", data={"content": TEXTO}, query_string={"normalizar_crlf": "on"}).status_code, 400)
        self.assertEqual(self.snapshot(), antes)

    def test_limite_da_requisicao_nao_registra_nem_trunca_texto(self):
        antes = self.snapshot()
        resposta = self.client.post("/envios/completo", data={"content": "x" * (2 * 1024 * 1024)})
        self.assertEqual(resposta.status_code, 413)
        self.assertEqual(self.snapshot(), antes)

    def test_fluxo_manual_continua_salvando_apenas_recebimento_e_preparacao(self):
        resposta = self.client.post("/envios", data={"content": TEXTO, "normalizar_crlf": "on"})
        self.assertEqual(resposta.status_code, 303)
        self.assertEqual(urlsplit(resposta.headers["Location"]).query, "")
        documento_id = int(urlsplit(resposta.headers["Location"]).path.rsplit("/", 1)[1])
        armazenados = self.cadeia(documento_id)
        self.assertEqual(set(armazenados), {"preparations"})
        antes = self.snapshot()
        self.assertEqual(self.client.get(resposta.headers["Location"]).status_code, 200)
        self.assertEqual(self.snapshot(), antes)

    def test_apos_gerar_tudo_usuario_ainda_escolhe_o_que_vetorizar(self):
        _, documento_id, selecao = self.enviar()
        antes = self.snapshot()
        resposta = self.client.post(f"/envios/{documento_id}/vetorizacoes", json={
            "contexto_execucao_id": selecao["contexto_execucao_id"][0],
            "perfil_id": "e5_simetrico", "opcoes": {"tipos_representacao": ["paragrafo", "documento"]},
        })
        self.assertEqual(resposta.status_code, 202, resposta.get_data(as_text=True))
        trabalho = resposta.get_json()
        self.assertEqual(trabalho["estado"], "enfileirada")
        self.assertEqual(trabalho["contexto_execucao_id"], selecao["contexto_execucao_id"][0])
        self.assertEqual(trabalho["perfil"]["tipos_representacao"], ["documento", "paragrafo"])
        depois = self.snapshot()
        for tabela in self.TABELAS:
            self.assertEqual(depois[tabela], antes[tabela])
        self.assertEqual(len(depois["vector_jobs"]), 1)
        self.assertEqual(depois["vector_blobs"], [])
        self.assertEqual(depois["vector_runs"], [])


if __name__ == "__main__":
    unittest.main()
