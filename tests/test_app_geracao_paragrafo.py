"""Geração editável por API, sem registro automático e com chave local persistida."""

from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from geracao_paragrafo import ErroGeracao


class FormControls(HTMLParser):
    """Inspeciona os controles reais sem depender de seletores de apresentação."""

    def __init__(self, html):
        super().__init__()
        self.controls = []
        self.form = None
        self.select = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "form":
            self.form = attributes.get("action")
        if tag in {"input", "textarea", "select", "button"}:
            control = {"tag": tag, "attrs": attributes, "form": self.form}
            self.controls.append(control)
            if tag == "select":
                control["options"] = []
                self.select = control
        if tag == "option" and self.select is not None:
            self.select["options"].append(attributes)

    def handle_endtag(self, tag):
        if tag == "form":
            self.form = None
        if tag == "select":
            self.select = None


class ParagraphGenerationAppTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "nested" / "textos.sqlite3"
        self.config_file = Path(directory.name) / "nested" / "geracao_chaves.json"
        self.config = {
            "TESTING": True,
            "DATABASE": str(self.database),
            "GENERATION_KEYS_FILE": str(self.config_file),
            "VECTORS_AUTO_START": False,
        }
        self.client = create_app(self.config).test_client()
        self.key = "sk-credencial-apenas-deste-pedido"
        self.payload = {
            "modelo": "modelo-escolhido-pelo-usuario",
        }
        self.producer = patch("app.gerar_paragrafo", return_value="Um parágrafo para editar.")
        self.generate = self.producer.start()
        self.addCleanup(self.producer.stop)

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return "\n".join(connection.iterdump())

    def assert_private_response(self, response, status):
        self.assertEqual(response.status_code, status, response.get_data(as_text=True))
        self.assertEqual(response.mimetype, "application/json")
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))
        self.assertNotIn(self.key, response.get_data(as_text=True))

    def save_key(self, key=None):
        response = self.client.post(
            "/api/geracao/configuracoes",
            json={"chave_api": self.key if key is None else key},
        )
        self.assert_private_response(response, 200)
        return response

    def test_saved_openrouter_key_is_reused_for_models_from_different_vendors(self):
        before = self.snapshot()
        self.save_key()
        for model in (
            "openai/gpt-modelo-escolhido",
            "anthropic/claude-modelo-escolhido",
        ):
            with self.subTest(model=model):
                self.generate.reset_mock()
                payload = dict(self.payload, modelo=model)
                response = self.client.post("/api/gerar-paragrafo", json=payload)
                self.assert_private_response(response, 200)
                self.assertEqual(response.get_json(), {"texto": "Um parágrafo para editar."})
                self.generate.assert_called_once_with("openrouter", model, self.key)
                self.assertEqual(self.snapshot(), before)
                self.assertNotIn(self.key, self.snapshot())

    def test_generation_does_not_change_existing_documents(self):
        self.save_key()
        saved = self.client.post("/envios", data={"content": "Texto original já registrado."})
        self.assertEqual(saved.status_code, 303)
        before = self.snapshot()
        response = self.client.post("/api/gerar-paragrafo", json=self.payload)
        self.assert_private_response(response, 200)
        self.assertEqual(self.snapshot(), before)

    def test_same_origin_and_local_clients_without_origin_are_allowed(self):
        self.save_key()
        for headers in ({}, {"Origin": "http://localhost"}):
            with self.subTest(headers=headers):
                self.assert_private_response(
                    self.client.post("/api/gerar-paragrafo", json=self.payload, headers=headers), 200,
                )
        response = self.client.post(
            "/api/gerar-paragrafo", json=self.payload,
            base_url="http://127.0.0.1:5001", headers={"Origin": "http://127.0.0.1:5001"},
        )
        self.assert_private_response(response, 200)

    def test_external_or_null_origin_is_rejected_before_provider_call(self):
        before = self.snapshot()
        for path, payload in (
            ("/api/gerar-paragrafo", self.payload),
            ("/api/geracao/configuracoes", {"chave_api": self.key}),
        ):
            for origin in ("https://externo.example", "http://localhost:5001", "https://localhost", "null"):
                with self.subTest(path=path, origin=origin):
                    response = self.client.post(path, json=payload, headers={"Origin": origin})
                    self.assert_private_response(response, 403)
        self.generate.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_invalid_or_non_object_json_never_calls_provider(self):
        for path in ("/api/gerar-paragrafo", "/api/geracao/configuracoes"):
            for body in ("{", "", "null", "[]", '"texto"', "123", "true"):
                with self.subTest(path=path, body=body):
                    response = self.client.post(path, data=body, content_type="application/json")
                    self.assert_private_response(response, 400)
        self.generate.assert_not_called()

    def test_extra_and_missing_fields_never_call_provider(self):
        for path, valid in (
            ("/api/gerar-paragrafo", self.payload),
            ("/api/geracao/configuracoes", {"chave_api": self.key}),
        ):
            invalid = [{}, dict(valid, endpoint="https://externo.example"), dict(valid, provedor="openai")]
            for field in valid:
                invalid.append({key: value for key, value in valid.items() if key != field})
            if path == "/api/gerar-paragrafo":
                invalid.append(dict(valid, chave_api=self.key))
            for payload in invalid:
                with self.subTest(path=path, fields=sorted(payload)):
                    response = self.client.post(path, json=payload)
                    self.assert_private_response(response, 400)
        self.generate.assert_not_called()

    def test_non_json_content_type_is_rejected(self):
        for path in ("/api/gerar-paragrafo", "/api/geracao/configuracoes"):
            for content_type in ("text/plain", "application/x-www-form-urlencoded"):
                with self.subTest(path=path, content_type=content_type):
                    response = self.client.post(path, data=json.dumps(self.payload), content_type=content_type)
                    self.assert_private_response(response, 415)
        self.generate.assert_not_called()

    def test_generation_has_its_own_smaller_request_size_limit(self):
        before = self.snapshot()
        response = self.client.post(
            "/api/gerar-paragrafo", json=dict(self.payload, modelo="x" * (16 * 1024)),
        )
        self.assert_private_response(response, 413)
        self.generate.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        # O limite da geração não reduz o limite de registro de textos da aplicação.
        saved = self.client.post("/envios", data={"content": "a" * (17 * 1024)})
        self.assertEqual(saved.status_code, 303)

    def test_configurations_have_same_small_request_size_limit(self):
        response = self.client.post(
            "/api/geracao/configuracoes",
            json={"chave_api": "x" * (16 * 1024)},
        )
        self.assert_private_response(response, 413)
        self.generate.assert_not_called()

    def test_missing_key_requires_configuration_without_calling_provider(self):
        response = self.client.post("/api/gerar-paragrafo", json=self.payload)
        self.assert_private_response(response, 400)
        self.assertTrue(response.get_json().get("erro"))
        self.generate.assert_not_called()

    def test_configuration_reports_presence_without_exposing_saved_keys(self):
        before = self.snapshot()
        response = self.client.get("/api/geracao/configuracoes")
        self.assert_private_response(response, 200)
        self.assertEqual(response.get_json(), {"chave_salva": False})
        self.save_key()
        response = self.client.get("/api/geracao/configuracoes")
        self.assert_private_response(response, 200)
        self.assertEqual(response.get_json(), {"chave_salva": True})
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn(self.key, self.snapshot())

    def test_saved_key_survives_app_restart_and_applies_to_other_models(self):
        self.save_key()
        restarted = create_app(self.config).test_client()
        response = restarted.get("/api/geracao/configuracoes")
        self.assert_private_response(response, 200)
        self.assertEqual(response.get_json(), {"chave_salva": True})
        payload = dict(self.payload, modelo="outro-modelo-escolhido")
        response = restarted.post("/api/gerar-paragrafo", json=payload)
        self.assert_private_response(response, 200)
        self.generate.assert_called_once_with("openrouter", payload["modelo"], self.key)

    def test_key_can_be_replaced_and_empty_key_removes_it_across_restarts(self):
        router_key = "sk-or-chave-substituta-router"
        self.save_key()
        response = self.save_key(router_key)
        self.assertNotIn(router_key, response.get_data(as_text=True))
        self.client.post("/api/gerar-paragrafo", json=self.payload)
        self.generate.assert_called_once_with("openrouter", self.payload["modelo"], router_key)
        self.save_key("")
        response = self.client.get("/api/geracao/configuracoes")
        self.assert_private_response(response, 200)
        self.assertEqual(response.get_json(), {"chave_salva": False})
        self.assertNotIn(router_key, response.get_data(as_text=True))
        self.generate.reset_mock()
        self.assert_private_response(self.client.post("/api/gerar-paragrafo", json=self.payload), 400)
        self.generate.assert_not_called()
        restarted = create_app(self.config).test_client()
        response = restarted.get("/api/geracao/configuracoes")
        self.assert_private_response(response, 200)
        self.assertEqual(response.get_json(), {"chave_salva": False})

    def test_provider_error_preserves_status_without_recording_request(self):
        self.save_key()
        before = self.snapshot()
        self.generate.side_effect = ErroGeracao("Limite de pedidos atingido. Tente novamente.", status_code=429)
        response = self.client.post("/api/gerar-paragrafo", json=self.payload)
        self.assert_private_response(response, 429)
        self.assertTrue(response.get_json().get("erro"))
        self.assertEqual(self.snapshot(), before)

    def test_editor_keeps_editable_text_and_empty_model_selector(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        controls = FormControls(html).controls
        self.assertIn("Gerar parágrafo", html)
        self.assertIn("Configurações", html)
        self.assertIn("Modelo · OpenRouter", html)
        editor = next(control for control in controls if control["attrs"].get("name") == "content")
        self.assertEqual(editor["tag"], "textarea")
        self.assertEqual(editor["form"], "/envios")
        self.assertNotIn("readonly", editor["attrs"])
        self.assertNotIn("disabled", editor["attrs"])
        model_selectors = [
            control for control in controls
            if control["tag"] == "select"
            and "model" in " ".join(str(value) for value in control["attrs"].values()).lower()
        ]
        self.assertEqual(len(model_selectors), 1)
        self.assertFalse(any(option.get("value") for option in model_selectors[0]["options"]))
        keys = [control for control in controls if control["attrs"].get("type") == "password"]
        self.assertEqual(len(keys), 1, "As configurações devem ter uma única chave do OpenRouter.")
        self.assertEqual(keys[0]["attrs"].get("id"), "generation-key")
        self.assertTrue(all(not control["attrs"].get("name") for control in keys))
        control_ids = {control["attrs"].get("id") for control in controls}
        self.assertTrue({"generation-save-key", "generation-remove-key"} <= control_ids)
        self.assertFalse(any(
            control["tag"] == "select" and "provider" in control["attrs"].get("id", "")
            for control in controls
        ))


if __name__ == "__main__":
    unittest.main()
