"""Contrato HTTP compartilhado por FastAPI e Flask, sem chamadas externas."""

from contextlib import closing
import csv
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from geracao_paragrafo import ErroGeracao


CHAVE = "sk-or-v1-credencial-privada-das-fixtures"
MODELO = "empresa/modelo-escolhido"
TEXTO = "Marina restaurou um mapa antigo na oficina. Depois levou a descoberta ao museu do bairro."
RAIZ = Path(__file__).resolve().parents[2]


class RespostaHTTP:
    """Uniformize apenas a leitura das respostas dos dois clientes oficiais."""

    def __init__(self, resposta):
        self.status_code = resposta.status_code
        self.headers = resposta.headers
        self.text = resposta.get_data(as_text=True) if hasattr(resposta, "get_data") else resposta.text

    def json(self):
        return json.loads(self.text)


class ContratoGeracaoHTTP:
    def setUp(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.database = Path(pasta.name) / "dados" / "textos.sqlite3"
        self.keys_file = Path(pasta.name) / "privado" / "geracao_chaves.json"
        ambiente = patch.dict(os.environ, {
            "GERADOR_PARAMETERS_FILE": str(RAIZ / "gerador" / "app" / "parameters.yaml"),
            "GERADOR_ENV_FILE": str(Path(pasta.name) / "sem-configuracao.env"),
            "GERADOR_DB": str(Path(pasta.name) / "importacao.sqlite3"),
            "GERADOR_KEYS_FILE": str(self.keys_file),
        })
        ambiente.start()
        self.addCleanup(ambiente.stop)
        self.client = self.criar_cliente()
        produtor = patch("gerador.app.storage.gerar_paragrafo", return_value={
            "texto": TEXTO,
            "metadados": {
                "provedor": "openrouter", "modelo_solicitado": MODELO,
                "modelo_resposta": MODELO, "id_resposta": "gen-simulado",
                "uso": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
                "finish_reason": "stop",
            },
        })
        self.gerar = produtor.start()
        self.addCleanup(produtor.stop)

    def get(self, caminho, **opcoes):
        return RespostaHTTP(self.client.get(caminho, **opcoes))

    def post(self, caminho, payload, **opcoes):
        return RespostaHTTP(self.client.post(caminho, json=payload, **opcoes))

    def assert_resposta(self, resposta, status):
        self.assertEqual(resposta.status_code, status, resposta.text)
        self.assertIn("no-store", resposta.headers.get("Cache-Control", ""))
        self.assertNotIn(CHAVE, resposta.text)
        return resposta.json()

    def salvar_chave(self):
        return self.assert_resposta(self.post("/api/geracao/configuracoes", {"chave_api": CHAVE}), 200)

    def criar_lote(self, **campos):
        payload = {"modelo": MODELO, "quantidade": 1, "parametros": {"tamanho": "curto"}, "temperatura": 0.8}
        payload.update(campos)
        return self.assert_resposta(self.post("/api/geracao/lotes", payload), 201)

    def assert_banco_sem_chave(self):
        with closing(sqlite3.connect(self.database)) as conexao:
            conteudo = "\n".join(conexao.iterdump())
        self.assertNotIn(CHAVE, conteudo)

    def test_catalogo_catorze_eixos_e_sorteio_concreto_sem_api(self):
        catalogo = self.assert_resposta(self.get("/api/geracao/catalogo"), 200)
        self.assertEqual(len(catalogo["parametros"]), 14)
        opcoes = {eixo["id"]: {opcao["valor"] for opcao in eixo["opcoes"]} for eixo in catalogo["parametros"]}
        self.assertEqual(catalogo["tamanho_frases"], {"curto": [2, 3], "medio": [4, 6], "longo": [7, 10]})
        self.assertEqual(catalogo["temperatura"], {"min": 0.0, "max": 1.2, "padrao": 0.8, "jitter_lote": 0.15, "suportada": False})
        sorteado = self.assert_resposta(self.post("/api/geracao/sortear", {}), 200)["parametros"]
        self.assertEqual(set(sorteado), set(opcoes))
        for eixo, valor in sorteado.items():
            self.assertIn(valor, opcoes[eixo])
            self.assertNotIn(valor, {"aleatorio", "variar"})
        self.gerar.assert_not_called()

    def test_criar_lote_planeja_sem_chave_ou_api_e_persiste_escolhas(self):
        lote = self.criar_lote(quantidade=3, parametros={"tamanho": "curto", "tom": "variar"})
        self.assertIs(type(lote["id"]), int)
        self.assertEqual((lote["status"], lote["concluidos"], lote["quantidade"]), ("planejado", 0, 3))
        self.assertEqual(lote["parametros_solicitados"], {"tamanho": "curto", "tom": "variar"})
        self.assertEqual(len(lote["itens"]), 3)
        for item in lote["itens"]:
            self.assertEqual(item["status"], "pendente")
            self.assertIsNone(item["prompt"])
            self.assertEqual(len(item["parametros"]), 14)
            self.assertEqual(item["parametros"]["tamanho"], "curto")
            self.assertTrue(all(valor not in {"aleatorio", "variar"} for valor in item["parametros"].values()))
            self.assertIsNone(item["temperatura"])
        self.assertEqual(self.assert_resposta(self.get(f"/api/geracao/lotes/{lote['id']}"), 200), lote)
        historico = self.assert_resposta(self.get("/api/geracao/historico"), 200)
        self.assertEqual(historico, {"lotes": [lote], "proximo_offset": None})
        self.gerar.assert_not_called()
        self.assertFalse(self.keys_file.exists())
        self.assert_banco_sem_chave()

    def test_proximo_precisa_chave_sem_reservar_item_ou_fazer_chamada(self):
        lote = self.criar_lote()
        self.assert_resposta(self.post(f"/api/geracao/lotes/{lote['id']}/proximo", {}), 400)
        self.assertEqual(self.assert_resposta(self.get(f"/api/geracao/lotes/{lote['id']}"), 200), lote)
        self.gerar.assert_not_called()

    def test_proximo_usa_chave_salva_e_finalizacao_nao_repete_chamada(self):
        self.assertEqual(self.salvar_chave(), {"chave_salva": True})
        lote = self.criar_lote()
        caminho = f"/api/geracao/lotes/{lote['id']}/proximo"
        final = self.assert_resposta(self.post(caminho, {}), 200)
        self.assertEqual((final["status"], final["aceitos"], final["concluidos"]), ("concluido", 1, 1))
        item = final["itens"][0]
        self.assertEqual(item["texto"], TEXTO)
        self.assertEqual(item["metadados"]["validacao"]["metricas"]["frases"], 2)
        self.assertEqual(item["metadados"]["uso"]["total_tokens"], 50)
        self.assertTrue(item["metadados"]["diversidade"]["aceito"])
        self.gerar.assert_called_once_with(
            "openrouter", MODELO, CHAVE, prompt=item["prompt"],
            retorno_detalhado=True, temperatura=None,
        )
        self.assertEqual(self.assert_resposta(self.post(caminho, {}), 200), final)
        self.gerar.assert_called_once()
        historico = self.assert_resposta(self.get("/api/geracao/historico"), 200)
        self.assertEqual(historico["lotes"], [final])
        self.assertEqual(self.assert_resposta(self.get("/api/geracao/configuracoes"), 200), {"chave_salva": True})
        self.assert_banco_sem_chave()

    def test_exportacoes_preservam_parametros_prompt_texto_e_metadados_sem_chave(self):
        self.salvar_chave()
        # Mesmo uma resposta simulada incorreta não pode levar a chave ao histórico.
        self.gerar.return_value["metadados"].update({"api_key": CHAVE, "detalhe": f"privado {CHAVE}"})
        lote = self.criar_lote()
        final = self.assert_resposta(self.post(f"/api/geracao/lotes/{lote['id']}/proximo", {}), 200)
        item = final["itens"][0]
        self.assertNotIn("api_key", item["metadados"])
        self.assertIn("[credencial removida]", item["metadados"]["detalhe"])
        base = f"/api/geracao/lotes/{lote['id']}/exportar"
        self.assertEqual(self.assert_resposta(self.get(base + ".json"), 200), final)
        resposta_jsonl = self.get(base + ".jsonl")
        resposta_csv = self.get(base + ".csv")
        for formato, resposta in (("jsonl", resposta_jsonl), ("csv", resposta_csv)):
            self.assertEqual(resposta.status_code, 200, resposta.text)
            self.assertIn("no-store", resposta.headers.get("Cache-Control", ""))
            self.assertIn(f'lote-{lote["id"]}.{formato}', resposta.headers["Content-Disposition"])
            self.assertNotIn(CHAVE, resposta.text)
        linhas_jsonl = [json.loads(linha) for linha in resposta_jsonl.text.splitlines()]
        self.assertEqual(linhas_jsonl, [{"lote": {chave: valor for chave, valor in final.items() if chave != "itens"}, "item": item}])
        linhas_csv = list(csv.DictReader(io.StringIO(resposta_csv.text.lstrip("\ufeff"))))
        self.assertEqual(len(linhas_csv), 1)
        linha = linhas_csv[0]
        self.assertEqual(linha["lote_id"], str(lote["id"]))
        self.assertEqual(linha["texto"], item["texto"])
        self.assertEqual(linha["prompt"], item["prompt"])
        self.assertEqual(json.loads(linha["parametros"]), item["parametros"])
        self.assertEqual(json.loads(linha["parametros_solicitados"]), final["parametros_solicitados"])
        self.assertEqual(json.loads(linha["metadados"]), item["metadados"])
        self.assert_banco_sem_chave()

    def test_campos_invalidos_nao_expoem_valores_recebidos_ou_criam_lote(self):
        padrao = {"modelo": MODELO, "quantidade": 1, "parametros": {"tamanho": "curto"}}
        invalidos = (
            {"modelo": ""}, {"modelo": [CHAVE]}, {"quantidade": 0}, {"quantidade": 21},
            {"quantidade": True}, {"quantidade": "2"}, {"parametros": {"inexistente": CHAVE}},
            {"parametros": {"tamanho": CHAVE}}, {"parametros": []},
            {"temperatura": True}, {"temperatura": "0.8"}, {"temperatura": -0.1}, {"temperatura": 1.3},
            {"chave_api": CHAVE},
        )
        for campos in invalidos:
            with self.subTest(campos=campos):
                resposta = self.post("/api/geracao/lotes", {**padrao, **campos})
                self.assertIn(resposta.status_code, (400, 422), resposta.text)
                self.assert_resposta(resposta, resposta.status_code)
        resposta_chave = self.post("/api/geracao/configuracoes", {"chave_api": {"valor": CHAVE}})
        self.assertIn(resposta_chave.status_code, (400, 422))
        self.assert_resposta(resposta_chave, resposta_chave.status_code)
        self.assertEqual(self.assert_resposta(self.get("/api/geracao/historico"), 200)["lotes"], [])
        self.gerar.assert_not_called()
        self.assert_banco_sem_chave()

    def test_erro_do_provedor_fica_no_historico_sem_expor_chave_ou_repetir(self):
        self.salvar_chave()
        lote = self.criar_lote()
        self.gerar.side_effect = ErroGeracao(f"A credencial {CHAVE} foi recusada.", 401)
        caminho = f"/api/geracao/lotes/{lote['id']}/proximo"
        final = self.assert_resposta(self.post(caminho, {}), 200)
        self.assertEqual((final["status"], final["erros"]), ("falhou", 1))
        self.assertEqual(final["itens"][0]["metadados"]["erro_status_code"], 401)
        self.assertEqual(self.assert_resposta(self.post(caminho, {}), 200), final)
        self.gerar.assert_called_once()
        self.assert_resposta(self.get("/api/geracao/historico"), 200)
        self.assert_resposta(self.get(f"/api/geracao/lotes/{lote['id']}/exportar.json"), 200)
        self.assert_banco_sem_chave()

    def test_origin_externo_nao_salva_chave_e_lote_inexistente_retorna_404(self):
        self.assert_resposta(self.post(
            "/api/geracao/configuracoes", {"chave_api": CHAVE}, headers={"Origin": "https://outro.invalid"},
        ), 403)
        self.assertEqual(self.assert_resposta(self.get("/api/geracao/configuracoes"), 200), {"chave_salva": False})
        self.assert_resposta(self.get("/api/geracao/lotes/99999"), 404)
        self.assert_resposta(self.get("/api/geracao/lotes/99999/exportar.json"), 404)
        self.gerar.assert_not_called()

    def test_novos_controles_avaliacao_e_regeneracao_preservam_banco_e_escolhas(self):
        self.salvar_chave()
        lote = self.criar_lote(
            tema="Memórias da infância", anti_repeticao=False, n_historico=7,
            seed=-5, modo_sorteio="uniforme", max_tentativas=2,
            parametros={"tipo_texto": "relato", "pessoa": "impessoal", "tamanho": "curto", "ousadia": "ousada"},
        )
        self.assertEqual(lote["controles"]["tema"], "Memórias da infância")
        self.assertEqual(lote["controles"]["seed"], -5)
        final = self.assert_resposta(self.post(f"/api/geracao/lotes/{lote['id']}/proximo", {}), 200)
        item = final["itens"][0]
        self.assertEqual(item["parametros"]["pessoa"], "impessoal")
        self.assertTrue(item["avisos"])
        self.assertIn("Memórias da infância", item["prompt"])
        self.assertEqual(len(item["tentativas"]), 1)
        path = f"/api/geracao/itens/{item['id']}"
        updated = self.client.patch(path + "/avaliacao", json={"aprovado": True})
        updated = RespostaHTTP(updated)
        self.assertEqual(self.assert_resposta(updated, 200)["avaliacao"], "aprovado")
        novo = self.assert_resposta(self.post(path + "/regenerar", {}), 201)
        self.assertNotEqual(novo["id"], lote["id"])
        self.assertEqual(novo["itens"][0]["parametros"], item["parametros"])
        self.assertEqual(novo["controles"]["tema"], "Memórias da infância")
        filtrado = self.assert_resposta(self.get(f"/api/geracao/historico?lote_id={lote['id']}"), 200)
        self.assertEqual([registro["id"] for registro in filtrado["lotes"]], [lote["id"]])
        self.assertEqual(filtrado["lotes"][0]["itens"][0]["texto"], item["texto"])
        self.assertEqual(filtrado["lotes"][0]["itens"][0]["avaliacao"], "aprovado")
        self.assert_banco_sem_chave()

    def test_capacidade_confirmada_habilita_temperatura_e_sorteio_seed_reproduz(self):
        capacidade = {"temperatura": {"suportada": True, "min": 0, "max": 1.2, "padrao": 0.8}, "avisos": []}
        with patch("app.capacidades_modelo", return_value=capacidade), patch("gerador.app.main.capacidades_modelo", return_value=capacidade):
            resposta = self.assert_resposta(self.get(f"/api/geracao/modelos/capacidades?modelo={MODELO}"), 200)
            self.assertTrue(resposta["temperatura"]["suportada"])
            lote = self.criar_lote()
            self.assertEqual(lote["itens"][0]["temperatura"], 0.8)
        primeira = self.assert_resposta(self.post("/api/geracao/sortear", {"seed": -7, "modo_sorteio": "uniforme"}), 200)
        segunda = self.assert_resposta(self.post("/api/geracao/sortear", {"seed": -7, "modo_sorteio": "uniforme"}), 200)
        self.assertEqual(primeira, segunda)
        self.assert_resposta(self.get("/api/geracao/modelos/capacidades?modelo=modelo%20invalido"), 400)


class FastAPIGeracaoTests(ContratoGeracaoHTTP, unittest.TestCase):
    def criar_cliente(self):
        from gerador.app.main import create_app

        self.application = create_app(database=self.database, keys_file=self.keys_file)
        self.assertEqual(Path(self.application.state.historico.database), self.database)
        self.assertEqual(Path(self.application.state.chaves.path), self.keys_file)
        cliente = TestClient(self.application)
        self.addCleanup(cliente.close)
        return cliente


class FlaskGeracaoTests(ContratoGeracaoHTTP, unittest.TestCase):
    def criar_cliente(self):
        from app import create_app

        self.application = create_app({
            "TESTING": True, "DATABASE": str(self.database),
            "GENERATION_KEYS_FILE": str(self.keys_file), "VECTORS_AUTO_START": False,
        })
        return self.application.test_client()


if __name__ == "__main__":
    unittest.main()
