"""Capacidades públicas e contrato LLM sem rede nem chamadas pagas."""

from concurrent.futures import ThreadPoolExecutor
import io
import json
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler

from api_narrativas import ErroAPINarrativa
from gerador.app import llm_client


MODELO = "empresa/modelo-exato"
CHAVE = "sk-or-v1-credencial-privada-de-teste"
TEXTO = "Luísa encontrou uma carta antiga. Depois a levou à biblioteca."


class RespostaCatalogo(io.BytesIO):
    def __init__(self, dados):
        super().__init__(dados)
        self.limite_lido = None

    def read(self, limite=-1):
        self.limite_lido = limite
        return super().read(limite)


class TestCapacidadesModelo(unittest.TestCase):
    def setUp(self):
        for nome, valor in (("_cache_catalogo", {}), ("_cache_expira", 0.0)):
            substituto = patch.object(llm_client, nome, valor)
            substituto.start()
            self.addCleanup(substituto.stop)
        relogio = patch.object(llm_client, "monotonic", return_value=1000.0)
        self.relogio = relogio.start()
        self.addCleanup(relogio.stop)
        construtor = patch.object(llm_client, "build_opener")
        self.construtor = construtor.start()
        self.addCleanup(construtor.stop)
        self.cliente_http = self.construtor.return_value
        self.contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        certificados = patch.object(llm_client, "_contexto_https", return_value=self.contexto)
        self.certificados = certificados.start()
        self.addCleanup(certificados.stop)

    def catalogo(self, registros):
        self.resposta = RespostaCatalogo(json.dumps({"data": registros}).encode())
        self.cliente_http.open.return_value = self.resposta

    def test_desconhecido_sem_consulta_nao_faz_rede(self):
        capacidades = llm_client.capacidades_modelo(MODELO)
        self.assertEqual(capacidades["temperatura"], {
            "suportada": False, "min": 0.0, "max": 1.2, "padrao": 0.8,
        })
        self.assertTrue(capacidades["avisos"])
        self.construtor.assert_not_called()
        self.certificados.assert_not_called()

    def test_get_publico_destino_fixo_tls_redirect_e_id_exato(self):
        self.catalogo([
            {"id": MODELO + "-extra", "supported_parameters": ["temperature"]},
            {"id": MODELO, "supported_parameters": ["max_tokens", "temperature"]},
            {"id": "outra/empresa", "supported_parameters": ["max_tokens"]},
        ])
        capacidades = llm_client.capacidades_modelo(MODELO, consultar=True)
        self.assertTrue(capacidades["temperatura"]["suportada"])
        self.assertEqual(capacidades["avisos"], [])
        pedido = self.cliente_http.open.call_args.args[0]
        self.assertEqual(pedido.full_url, "https://openrouter.ai/api/v1/models")
        self.assertEqual(pedido.get_method(), "GET")
        self.assertIsNone(pedido.data)
        self.assertEqual({nome.lower(): valor for nome, valor in pedido.header_items()}, {"accept": "application/json"})
        self.assertEqual(self.cliente_http.open.call_args.kwargs, {"timeout": 10})
        self.assertEqual(self.resposta.limite_lido, 8 * 1024 * 1024 + 1)
        handlers = self.construtor.call_args.args
        self.assertTrue(any(isinstance(handler, llm_client._SemRedirecionamento) for handler in handlers))
        https = next(handler for handler in handlers if isinstance(handler, HTTPSHandler))
        self.assertIs(https._context, self.contexto)
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)
        self.assertIsNone(handlers[0].redirect_request(Mock(), None, 302, "Found", {}, "https://outro.example"))
        self.certificados.assert_called_once_with()
        self.assertFalse(llm_client.capacidades_modelo("outra/empresa")["temperatura"]["suportada"])
        self.cliente_http.open.assert_called_once()

    def test_prefixo_de_modelo_nao_confirma_capacidade(self):
        self.catalogo([{"id": MODELO + "-extra", "supported_parameters": ["temperature"]}])
        capacidades = llm_client.capacidades_modelo(MODELO, consultar=True)
        self.assertFalse(capacidades["temperatura"]["suportada"])
        self.assertIn("não foi encontrado", capacidades["avisos"][0])

    def test_catalogo_requer_lista_e_parametro_temperature_exato(self):
        for parametros in (["top_p"], ["Temperature"], {"temperature": True}, None):
            with self.subTest(parametros=parametros):
                llm_client._cache_expira = 0
                self.catalogo([{"id": MODELO, "supported_parameters": parametros}])
                capacidades = llm_client.capacidades_modelo(MODELO, consultar=True)
                self.assertFalse(capacidades["temperatura"]["suportada"])
                self.assertIn("não declara suporte", capacidades["avisos"][0])

    def test_cache_ttl_de_cinco_minutos_sem_rede_implicita(self):
        self.catalogo([{"id": MODELO, "supported_parameters": ["temperature"]}])
        self.assertTrue(llm_client.capacidades_modelo(MODELO, consultar=True)["temperatura"]["suportada"])
        self.relogio.return_value = 1299.0
        self.assertTrue(llm_client.capacidades_modelo(MODELO, consultar=True)["temperatura"]["suportada"])
        self.relogio.return_value = 1300.0
        self.assertFalse(llm_client.capacidades_modelo(MODELO)["temperatura"]["suportada"])
        self.cliente_http.open.assert_called_once()
        self.catalogo([{"id": MODELO, "supported_parameters": []}])
        self.assertFalse(llm_client.capacidades_modelo(MODELO, consultar=True)["temperatura"]["suportada"])
        self.assertEqual(self.cliente_http.open.call_count, 2)

    def test_resultados_mutaveis_nao_alteram_o_cache(self):
        self.catalogo([{"id": MODELO, "supported_parameters": ["temperature"]}])
        capacidades = llm_client.capacidades_modelo(MODELO, consultar=True)
        capacidades["temperatura"]["suportada"] = False
        capacidades["avisos"].append("alterado")
        self.assertEqual(llm_client.capacidades_modelo(MODELO)["avisos"], [])
        self.assertTrue(llm_client.capacidades_modelo(MODELO)["temperatura"]["suportada"])

    def test_consultas_concorrentes_compartilham_cache(self):
        self.catalogo([{"id": MODELO, "supported_parameters": ["temperature"]}])
        with ThreadPoolExecutor(max_workers=5) as executor:
            resultados = list(executor.map(lambda _: llm_client.capacidades_modelo(MODELO, consultar=True), range(10)))
        self.assertTrue(all(resultado["temperatura"]["suportada"] for resultado in resultados))
        self.cliente_http.open.assert_called_once()

    def test_resposta_invalida_e_grande_devolve_aviso_seguro(self):
        for corpo in (b"segredo remoto ilegivel", b"{}", b"[]", b'{"data": {}}', b"x" * (8 * 1024 * 1024 + 1)):
            with self.subTest(tamanho=len(corpo)):
                self.cliente_http.open.return_value = RespostaCatalogo(corpo)
                capacidades = llm_client.capacidades_modelo(MODELO, consultar=True)
                self.assertFalse(capacidades["temperatura"]["suportada"])
                self.assertEqual(capacidades["avisos"], [llm_client._AVISO_CONSULTA])
                self.assertNotIn("segredo remoto", json.dumps(capacidades))

    def test_falhas_de_rede_e_certificados_nao_expoem_detalhes(self):
        for erro in (URLError(CHAVE), TimeoutError(CHAVE), ssl.SSLError(CHAVE), ErroAPINarrativa(CHAVE)):
            with self.subTest(erro=type(erro).__name__):
                self.cliente_http.open.side_effect = erro
                capacidades = llm_client.capacidades_modelo(MODELO, consultar=True)
                self.assertFalse(capacidades["temperatura"]["suportada"])
                self.assertEqual(capacidades["avisos"], [llm_client._AVISO_CONSULTA])
                self.assertNotIn(CHAVE, json.dumps(capacidades))
        self.cliente_http.open.side_effect = None
        self.certificados.side_effect = ErroAPINarrativa(CHAVE)
        self.assertEqual(llm_client.capacidades_modelo(MODELO, consultar=True)["avisos"], [llm_client._AVISO_CONSULTA])

    def test_erro_http_nao_le_corpo_e_fecha_resposta(self):
        corpo = Mock()
        erro = HTTPError("https://openrouter.ai/api/v1/models", 302, CHAVE, {}, corpo)
        self.cliente_http.open.side_effect = erro
        capacidades = llm_client.capacidades_modelo(MODELO, consultar=True)
        self.assertFalse(capacidades["temperatura"]["suportada"])
        self.assertNotIn(CHAVE, json.dumps(capacidades))
        corpo.read.assert_not_called()
        corpo.close.assert_called_once()

    def test_modelo_invalido_gera_erro_400_sem_rede(self):
        for modelo in ("", None, 1, "empresa/modelo com espaços", "modelo\n", "x" * 201):
            with self.subTest(modelo=modelo):
                with self.assertRaises(llm_client.ErroGeracao) as erro:
                    llm_client.capacidades_modelo(modelo, consultar=True)
                self.assertEqual(erro.exception.status_code, 400)
        self.construtor.assert_not_called()

    def test_consultar_deve_ser_booleano_para_evitar_rede_acidental(self):
        with self.assertRaises(llm_client.ErroGeracao):
            llm_client.capacidades_modelo(MODELO, consultar="false")
        self.construtor.assert_not_called()


class TestContratoLLM(unittest.TestCase):
    def test_cliente_satisfaz_protocolo_e_nao_expoe_chave_no_repr(self):
        cliente = llm_client.OpenRouterClient(MODELO, CHAVE)
        self.assertIsInstance(cliente, llm_client.LLMClient)
        self.assertNotIn(CHAVE, repr(cliente))

    @patch.object(llm_client._transporte, "gerar_paragrafo", return_value=TEXTO)
    def test_cliente_suprime_temperatura_desconhecida_e_respeita_max_tokens(self, transporte):
        with patch.object(llm_client, "capacidades_modelo", return_value={"temperatura": {"suportada": False}}) as capacidades:
            resultado = llm_client.OpenRouterClient(MODELO, CHAVE).gerar("Escreva um parágrafo.", 777, 0.8)
        self.assertEqual(resultado, TEXTO)
        capacidades.assert_called_once_with(MODELO, consultar=False)
        transporte.assert_called_once_with(
            "openrouter", MODELO, CHAVE, prompt="Escreva um parágrafo.",
            temperatura=None, retorno_detalhado=False, max_tokens=777,
        )

    @patch.object(llm_client._transporte, "gerar_paragrafo", return_value=TEXTO)
    def test_cliente_envia_temperatura_apenas_quando_suportada(self, transporte):
        with patch.object(llm_client, "capacidades_modelo", return_value={"temperatura": {"suportada": True}}):
            llm_client.OpenRouterClient(MODELO, CHAVE).gerar("Escreva um parágrafo.", 4096, 1.2)
        self.assertEqual(transporte.call_args.kwargs["temperatura"], 1.2)
        self.assertEqual(transporte.call_args.kwargs["max_tokens"], 4096)
        self.assertNotIn("top_p", transporte.call_args.kwargs)
        self.assertNotIn("top_k", transporte.call_args.kwargs)

    def test_cliente_valida_modelo_e_chave_antes_da_geracao(self):
        for modelo, chave in (("", CHAVE), (MODELO, ""), ("empresa/modelo invalido", CHAVE)):
            with self.subTest(modelo=modelo):
                with self.assertRaises(llm_client.ErroGeracao) as erro:
                    llm_client.OpenRouterClient(modelo, chave)
                self.assertEqual(erro.exception.status_code, 400)

    @patch.object(llm_client._transporte, "gerar_paragrafo")
    def test_funcao_compativel_preserva_retorno_detalhado_e_max_tokens(self, transporte):
        transporte.return_value = {"texto": TEXTO, "metadados": {}}
        resposta = llm_client.gerar_paragrafo(
            "openrouter", MODELO, CHAVE, prompt="Prompt", temperatura=0.5,
            retorno_detalhado=True, max_tokens=678,
        )
        self.assertIs(resposta, transporte.return_value)
        transporte.assert_called_once_with(
            "openrouter", MODELO, CHAVE, prompt="Prompt", temperatura=0.5,
            retorno_detalhado=True, max_tokens=678,
        )

    @patch.object(llm_client._transporte, "gerar_paragrafo", return_value=TEXTO)
    def test_funcao_compativel_sem_max_tokens_preserva_chamada_antiga(self, transporte):
        self.assertEqual(llm_client.gerar_paragrafo("openrouter", MODELO, CHAVE), TEXTO)
        transporte.assert_called_once_with(
            "openrouter", MODELO, CHAVE, prompt=None, temperatura=None, retorno_detalhado=False,
        )


if __name__ == "__main__":
    unittest.main()
