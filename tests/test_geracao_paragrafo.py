"""Cobertura da geração com transporte simulado, sem chamadas pagas."""

import io
import json
import socket
import ssl
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler

import geracao_paragrafo as geracao


def resposta(texto="Ana levou um livro à praça e encontrou uma amiga.", **campos):
    escolha = {"message": {"content": texto}, "finish_reason": "stop"}
    escolha.update(campos)
    return {"choices": [escolha]}


class GeracaoParagrafoTests(unittest.TestCase):
    def setUp(self):
        espera = patch.object(geracao.time, "sleep")
        self.esperar = espera.start()
        self.addCleanup(espera.stop)

    def chamar(self, dados=None, erro=None, provedor="openrouter", modelo="modelo-escolhido", **opcoes):
        cliente = MagicMock()
        if erro:
            cliente.open.side_effect = erro
        else:
            corpo = json.dumps(resposta() if dados is None else dados).encode("utf-8")
            cliente.open.return_value.__enter__.return_value.read.return_value = corpo
        with patch.object(geracao, "build_opener", return_value=cliente) as construir:
            texto = geracao.gerar_paragrafo(provedor, modelo, "sk-or-v1-chave-privada", **opcoes)
        return texto, cliente, construir

    def chamar_sequencia(self, eventos, **opcoes):
        cliente = MagicMock()
        respostas = []
        for evento in eventos:
            if isinstance(evento, Exception):
                respostas.append(evento)
            else:
                resposta_http = MagicMock()
                resposta_http.__enter__.return_value.read.return_value = json.dumps(evento).encode("utf-8")
                respostas.append(resposta_http)
        cliente.open.side_effect = respostas
        with patch.object(geracao, "build_opener", return_value=cliente):
            resultado = geracao.gerar_paragrafo("openrouter", "modelo", "sk-or-v1-chave-privada", **opcoes)
        return resultado, cliente

    def test_openrouter_recebe_modelos_diversos_com_a_mesma_chave(self):
        for modelo in ("openai/gpt-escolhido", "anthropic/claude-escolhido", "empresa/modelo:versao"):
            with self.subTest(modelo=modelo):
                texto, cliente, construir = self.chamar(modelo=modelo)
                self.assertIn("Ana", texto)
                pedido = cliente.open.call_args.args[0]
                self.assertEqual(pedido.full_url, "https://openrouter.ai/api/v1/chat/completions")
                self.assertEqual(pedido.get_method(), "POST")
                self.assertEqual(pedido.get_header("Authorization"), "Bearer sk-or-v1-chave-privada")
                corpo = json.loads(pedido.data)
                self.assertEqual(corpo["model"], modelo)
                self.assertEqual(corpo["max_tokens"], 2048)
                self.assertNotIn("temperature", corpo)
                self.assertNotIn("chave-privada", pedido.data.decode())
                self.assertEqual(cliente.open.call_args.kwargs["timeout"], geracao.TIMEOUT_SEGUNDOS)
                self.assertEqual(cliente.open.return_value.__enter__.return_value.read.call_args.args, (geracao.LIMITE_RESPOSTA_BYTES + 1,))
                sem_redirect, https = construir.call_args.args
                self.assertIsInstance(sem_redirect, geracao._SemRedirecionamento)
                self.assertIsInstance(https, HTTPSHandler)
                self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(https._context.check_hostname)
                self.assertIsNone(sem_redirect.redirect_request(pedido, None, 302, "", {}, "https://outro.invalid"))

    def test_rejeita_entradas_invalidas_antes_da_rede(self):
        invalidos = (
            ("openai", "modelo", "chave"), ("anthropic", "modelo", "chave"), ([], "modelo", "chave"),
            ("openrouter", None, "chave"), ("openrouter", "", "chave"),
            ("openrouter", "modelo\n", "chave"), ("openrouter", "modelo\u200b", "chave"),
            ("openrouter", "meu modelo", "chave"), ("openrouter", "x" * 201, "chave"),
            ("openrouter", "modelo", ""), ("openrouter", "modelo", False),
            ("openrouter", "modelo", "chave\r\nX:segredo"),
            ("openrouter", "modelo", "chave com espaço"), ("openrouter", "modelo", "x" * 4097),
        )
        for argumentos in invalidos:
            with self.subTest(argumentos=argumentos), patch.object(geracao, "build_opener") as construir:
                with self.assertRaises(geracao.ErroGeracao) as capturado:
                    geracao.gerar_paragrafo(*argumentos)
                self.assertEqual(capturado.exception.status_code, 400)
                construir.assert_not_called()

    def test_prompt_personalizado_substitui_prompt_padrao_e_admite_paragrafo_maior(self):
        prompt = "Gere um parágrafo de 10 frases em português.\nUse uma cena cotidiana."
        esperado = " ".join(["palavra"] * 200) + "."
        texto, cliente, _ = self.chamar(resposta(esperado), prompt=prompt, temperatura=0.8)
        self.assertEqual(texto, esperado)
        corpo = json.loads(cliente.open.call_args.args[0].data)
        self.assertEqual(corpo["messages"], [{"role": "user", "content": prompt}])
        self.assertEqual(corpo["max_tokens"], 4096)
        self.assertEqual(corpo["temperature"], 0.8)
        cliente.open.assert_called_once()

    def test_prompt_personalizado_preserva_limites_defensivos_e_paragrafo_unico(self):
        for texto in ("palavra " * 601, "a" * 10001, "Um texto.\n\nOutro texto.", "<b>Um texto.</b>", "*Um texto.*"):
            with self.subTest(tamanho=len(texto)), self.assertRaises(geracao.ErroGeracao) as capturado:
                self.chamar(resposta(texto), prompt="Gere um texto.")
            self.assertEqual(capturado.exception.status_code, 502)

    def test_valida_prompt_temperatura_e_opcao_de_retorno_antes_da_rede(self):
        invalidos = (
            {"prompt": False}, {"prompt": []}, {"prompt": ""}, {"prompt": " \n\t"},
            {"prompt": "p" * 20001}, {"prompt": "prompt\x00"}, {"prompt": "prompt\ud800"},
            {"temperatura": True}, {"temperatura": "0.8"}, {"temperatura": -0.01},
            {"temperatura": 1.21}, {"temperatura": float("nan")},
            {"temperatura": float("inf")}, {"temperatura": 10 ** 1000},
            {"retorno_detalhado": "sim"}, {"retorno_detalhado": 1}, {"retorno_detalhado": None},
            {"max_tokens": 0}, {"max_tokens": True}, {"max_tokens": 16385}, {"max_tokens": "512"},
        )
        for opcoes in invalidos:
            with self.subTest(opcoes=opcoes), patch.object(geracao, "build_opener") as construir:
                with self.assertRaises(geracao.ErroGeracao) as capturado:
                    geracao.gerar_paragrafo("openrouter", "modelo", "chave-privada", **opcoes)
                self.assertEqual(capturado.exception.status_code, 400)
                construir.assert_not_called()

    def test_aceita_limites_de_temperatura(self):
        for temperatura in (0, 0.0, 1, 1.2):
            with self.subTest(temperatura=temperatura):
                _, cliente, _ = self.chamar(temperatura=temperatura)
                self.assertEqual(json.loads(cliente.open.call_args.args[0].data)["temperature"], temperatura)

    def test_retorno_detalhado_contem_somente_metadados_seguros(self):
        dados = resposta()
        dados.update({
            "id": "gen-123", "model": "empresa/modelo-real",
            "usage": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50, "segredo": "privado"},
            "chave_api": "sk-or-v1-chave-privada",
        })
        resultado, cliente, _ = self.chamar(dados, retorno_detalhado=True)
        self.assertEqual(resultado, {
            "texto": "Ana levou um livro à praça e encontrou uma amiga.",
            "metadados": {
                "provedor": "openrouter", "modelo_solicitado": "modelo-escolhido",
                "modelo_resposta": "empresa/modelo-real", "id_resposta": "gen-123",
                "uso": {"prompt_tokens": 30, "completion_tokens": 20, "total_tokens": 50},
                "finish_reason": "stop",
                "tentativas_rede": 1, "avisos": [], "temperatura_enviada": None,
            },
        })
        self.assertNotIn("sk-or-v1-chave-privada", json.dumps(resultado))
        self.assertNotIn("segredo", json.dumps(resultado))
        cliente.open.assert_called_once()

    def test_limite_de_tokens_explicito_e_respeitado(self):
        _, cliente, _ = self.chamar(prompt="Gere texto.", max_tokens=512)
        self.assertEqual(json.loads(cliente.open.call_args.args[0].data)["max_tokens"], 512)

    def test_timeout_429_e_erro_5xx_repetem_com_backoff_e_limite_total(self):
        for erro in (
            TimeoutError("segredo"), URLError(socket.gaierror(-2, "segredo")),
            HTTPError("https://privado.invalid", 429, "segredo", {}, None),
            HTTPError("https://privado.invalid", 503, "segredo", {}, None),
        ):
            with self.subTest(erro=type(erro).__name__, status=getattr(erro, "code", None)):
                self.esperar.reset_mock()
                resultado, cliente = self.chamar_sequencia([erro, erro, resposta()], retorno_detalhado=True)
                self.assertEqual(cliente.open.call_count, 3)
                self.assertEqual(resultado["metadados"]["tentativas_rede"], 3)
                self.assertEqual([chamada.args[0] for chamada in self.esperar.call_args_list], [0.5, 1.0])
                self.assertNotIn("segredo", json.dumps(resultado))

    def test_retry_after_tem_teto_de_cinco_segundos(self):
        erro = HTTPError("https://privado.invalid", 429, "segredo", {"Retry-After": "3600"}, None)
        resultado, cliente = self.chamar_sequencia([erro, resposta()], retorno_detalhado=True)
        self.esperar.assert_called_once_with(5.0)
        self.assertEqual(cliente.open.call_count, 2)
        self.assertEqual(resultado["metadados"]["tentativas_rede"], 2)

    def test_fallback_de_amostragem_repete_sem_temperatura_e_registra_aviso(self):
        corpo = io.BytesIO(b'{"error":{"message":"temperature unsupported sk-or-v1-chave-privada"}}')
        erro = HTTPError("https://privado.invalid", 400, "segredo", {}, corpo)
        resultado, cliente = self.chamar_sequencia([erro, resposta()], temperatura=0.8, retorno_detalhado=True)
        primeiro, segundo = [json.loads(chamada.args[0].data) for chamada in cliente.open.call_args_list]
        self.assertEqual(primeiro["temperature"], 0.8)
        self.assertNotIn("temperature", segundo)
        for pedido in (primeiro, segundo):
            self.assertNotIn("top_p", pedido)
            self.assertNotIn("top_k", pedido)
        self.assertEqual(resultado["metadados"]["tentativas_rede"], 2)
        self.assertIsNone(resultado["metadados"]["temperatura_enviada"])
        self.assertEqual(len(resultado["metadados"]["avisos"]), 1)
        self.assertNotIn("sk-or-v1-chave-privada", json.dumps(resultado))
        self.esperar.assert_not_called()

    def test_fallback_conta_no_limite_total_de_tres_envios(self):
        erro400 = HTTPError("https://privado.invalid", 400, "segredo", {}, io.BytesIO(b"unsupported top_p"))
        resultado, cliente = self.chamar_sequencia(
            [TimeoutError("segredo"), erro400, resposta()], temperatura=1.0, retorno_detalhado=True,
        )
        self.assertEqual(cliente.open.call_count, 3)
        self.assertEqual(resultado["metadados"]["tentativas_rede"], 3)
        self.assertIsNone(resultado["metadados"]["temperatura_enviada"])
        self.esperar.assert_called_once_with(0.5)

    def test_corpo_de_erro_400_e_lido_com_limite_sem_expor_credencial(self):
        corpo = MagicMock(wraps=io.BytesIO(b"temperature " + b"x" * geracao.LIMITE_ERRO_HTTP_BYTES))
        erro = HTTPError("https://privado.invalid", 400, "segredo", {}, corpo)
        with self.assertRaises(geracao.ErroGeracao) as capturado:
            self.chamar(erro=erro, temperatura=0.8)
        corpo.read.assert_called_once_with(geracao.LIMITE_ERRO_HTTP_BYTES + 1)
        self.assertEqual(capturado.exception.metadados["tentativas_rede"], 1)
        self.assertEqual(capturado.exception.tipo, "api")
        self.assertIsNone(capturado.exception.texto_gerado)
        self.assertNotIn("segredo", str(capturado.exception))

    def test_metadados_malformados_sao_removidos_sem_expor_credencial(self):
        cenarios = (
            {"id": "segredo\n", "model": ["modelo"], "usage": []},
            {"id": "prefixo-sk-or-v1-chave-privada", "model": "m" * 201,
             "usage": {"prompt_tokens": True, "completion_tokens": -1, "total_tokens": 1.5}},
            {"id": "i" * 201, "model": "sk-or-v1-chave-privada",
             "usage": {"prompt_tokens": "10", "completion_tokens": 10 ** 20, "total_tokens": None}},
        )
        for campos in cenarios:
            dados = resposta()
            dados.update(campos)
            with self.subTest(campos=campos):
                resultado, _, _ = self.chamar(dados, modelo="sk-or-v1-chave-privada", retorno_detalhado=True)
                metadados = resultado["metadados"]
                self.assertIsNone(metadados["modelo_solicitado"])
                self.assertIsNone(metadados["modelo_resposta"])
                self.assertIsNone(metadados["id_resposta"])
                self.assertEqual(metadados["uso"], {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None})
                self.assertNotIn("sk-or-v1-chave-privada", json.dumps(resultado))

    def test_truncamento_nao_causa_repeticao_automatica(self):
        cliente = MagicMock()
        cliente.open.return_value.__enter__.return_value.read.return_value = json.dumps(
            resposta("Parágrafo incompleto", finish_reason="length")
        ).encode("utf-8")
        with patch.object(geracao, "build_opener", return_value=cliente):
            with self.assertRaises(geracao.ErroGeracao) as capturado:
                geracao.gerar_paragrafo("openrouter", "modelo", "chave-privada", prompt="Gere dez frases.")
        cliente.open.assert_called_once()
        self.assertEqual(capturado.exception.tipo, "conteudo")
        self.assertEqual(capturado.exception.texto_gerado, "Parágrafo incompleto")
        self.assertEqual(capturado.exception.metadados["tentativas_rede"], 1)

    def test_normaliza_quebras_suaves_sem_alterar_acentos(self):
        texto, _, _ = self.chamar(resposta("  Ana chegou à praça.\r\nEla\t encontrou João.  "))
        self.assertEqual(texto, "Ana chegou à praça. Ela encontrou João.")

    def test_rejeita_resposta_vazia_multipla_formatada_recusa_longa_ou_truncada(self):
        invalidas = (
            resposta(""), resposta(None), resposta("Um texto.\n\nOutro texto."),
            resposta("Um texto.\n \t\nOutro texto."), resposta("Um texto.\u2029Outro texto."),
            resposta("# Um título"), resposta("**Um texto**"), resposta("1. Um item"),
            resposta("Não posso atender a essa solicitação."),
            resposta("Sinto muito, não posso gerar esse texto."),
            resposta("I cannot provide that content."), resposta("palavra " * 151),
            resposta("Texto incompleto", finish_reason="length"),
            resposta("Texto bloqueado", finish_reason="content_filter"),
            resposta("Texto parcial", finish_reason=None),
            resposta(message={"content": "Texto", "refusal": "segredo"}),
            resposta("Texto\x00inválido"), {}, {"choices": []}, {"choices": [None]},
            {"error": {"message": "segredo"}}, {"choices": [{"finish_reason": "stop"}]}, [],
        )
        for dados in invalidas:
            with self.subTest(dados=dados), self.assertRaises(geracao.ErroGeracao) as capturado:
                self.chamar(dados)
            self.assertEqual(capturado.exception.status_code, 502)
            self.assertNotIn("segredo", str(capturado.exception))

    def test_erro_http_nao_le_corpo_nem_expoe_detalhes(self):
        for codigo, esperado in ((400, 400), (401, 401), (402, 402), (403, 403), (404, 400), (429, 429), (500, 502), (302, 502)):
            corpo = MagicMock(wraps=io.BytesIO(b"chave-privada corpo-secreto"))
            erro = HTTPError("https://privado.invalid", codigo, "mensagem-privada", {}, corpo)
            with self.subTest(codigo=codigo), self.assertRaises(geracao.ErroGeracao) as capturado:
                self.chamar(erro=erro)
            self.assertEqual(capturado.exception.status_code, esperado)
            for segredo in ("privado.invalid", "chave-privada", "corpo-secreto", "mensagem-privada"):
                self.assertNotIn(segredo, str(capturado.exception))
            corpo.read.assert_not_called()
            tentativas = 3 if codigo in (429, 500) else 1
            corpo.close.assert_called_once()
            self.assertEqual(capturado.exception.metadados["tentativas_rede"], tentativas)

    def test_erros_de_rede_e_tls_tem_mensagem_segura(self):
        for motivo, status, trecho in (
            (ssl.SSLCertVerificationError(1, "segredo"), 502, "certificado"),
            (ssl.SSLError(1, "segredo"), 502, "HTTPS segura"),
            (socket.timeout("segredo"), 504, "demorou"),
            (socket.gaierror(-2, "segredo"), 502, "conectar"),
        ):
            for erro in (motivo, URLError(motivo)):
                with self.subTest(erro=type(erro).__name__, motivo=type(motivo).__name__), self.assertRaises(geracao.ErroGeracao) as capturado:
                    self.chamar(erro=erro)
                self.assertEqual(capturado.exception.status_code, status)
                self.assertIn(trecho, str(capturado.exception))
                self.assertNotIn("segredo", str(capturado.exception))
                self.assertEqual(capturado.exception.metadados["tentativas_rede"], 1 if isinstance(motivo, ssl.SSLError) else 3)

    def test_json_ilegivel_grande_ou_aninhado_demais_e_rejeitado(self):
        for corpo in (b"nao-json segredo", b"\xff", b"x" * (geracao.LIMITE_RESPOSTA_BYTES + 1), b"[" * 2000 + b"]" * 2000):
            cliente = MagicMock()
            cliente.open.return_value.__enter__.return_value.read.return_value = corpo
            with self.subTest(tamanho=len(corpo)), patch.object(geracao, "build_opener", return_value=cliente), self.assertRaises(geracao.ErroGeracao) as capturado:
                geracao.gerar_paragrafo("openrouter", "modelo", "chave-privada")
            self.assertEqual(capturado.exception.status_code, 502)
            self.assertNotIn("segredo", str(capturado.exception))
            self.assertEqual(capturado.exception.tipo, "conteudo")
            self.assertIsNone(capturado.exception.texto_gerado)

    def test_falha_ao_carregar_certificados_nao_faz_requisicao(self):
        erro = geracao.ErroAPINarrativa("segredo https://usuario:senha@privado.invalid")
        with patch.object(geracao, "_contexto_https", side_effect=erro) as contexto, patch.object(geracao, "build_opener") as construir:
            with self.assertRaises(geracao.ErroGeracao) as capturado:
                geracao.gerar_paragrafo("openrouter", "modelo", "chave-privada")
        self.assertEqual(capturado.exception.status_code, 502)
        self.assertIn("certificados HTTPS", str(capturado.exception))
        self.assertNotIn("segredo", str(capturado.exception))
        contexto.assert_called_once_with()
        construir.assert_not_called()

    def test_usa_contexto_https_compartilhado_sem_mudar_a_confianca_tls(self):
        contexto = ssl.create_default_context()
        with patch.object(geracao, "_contexto_https", return_value=contexto) as contexto_compartilhado:
            _, _, construir = self.chamar()
        contexto_compartilhado.assert_called_once_with()
        self.assertIs(construir.call_args.args[1]._context, contexto)
        self.assertEqual(contexto.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(contexto.check_hostname)


if __name__ == "__main__":
    unittest.main()
