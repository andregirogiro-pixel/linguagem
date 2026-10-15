from pathlib import Path
import os
import sqlite3
import tempfile
import unittest

from jinja2 import FileSystemLoader

from app import create_app


class TextRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "nested" / "textos.sqlite3"
        self.config = {"TESTING": True, "DATABASE": str(self.database)}
        self.client = create_app(self.config).test_client()

    def records(self):
        with sqlite3.connect(self.database) as connection:
            return connection.execute("SELECT id, content, created_at FROM submissions").fetchall()

    def test_original_text_persists_after_app_restart(self):
        content = "  Olá, linguagem!\r\nSegunda linha.\nCitação: 'ação'; 漢字 📝  "
        response = self.client.post("/envios", data={"content": content})
        self.assertEqual(response.status_code, 303)
        rows = self.records()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], content)
        self.assertTrue(rows[0][2].endswith("+00:00"))
        new_client = create_app(self.config).test_client()
        confirmation = new_client.get(response.headers["Location"])
        self.assertEqual(confirmation.status_code, 200)
        self.assertIn("Texto registrado", confirmation.get_data(as_text=True))
        # Atualizar a confirmação não repete o envio.
        new_client.get(response.headers["Location"])
        self.assertEqual(len(self.records()), 1)

    def test_template_update_appears_without_restarting_or_changing_saved_text(self):
        content = "Maria chegou. João saiu.\n\nEla voltou."
        response = self.client.post("/envios", data={"content": content})
        self.assertEqual(response.status_code, 303)
        location = response.headers["Location"]
        segmented = self.client.post(location + "/segmentacoes", json={})
        self.assertEqual(segmented.status_code, 201)

        application = self.client.application
        self.assertFalse(application.debug)
        current_template = (Path(application.root_path) / "templates" / "index.html").read_text(encoding="utf-8")
        template_directory = Path(self.directory.name) / "templates"
        template_directory.mkdir()
        template = template_directory / "index.html"
        template.write_text(
            '<details class="hierarchy-paragraph"><summary>Parágrafo 1</summary>'
            '<details class="hierarchy-period">'
            '<summary><span class="hierarchy-unit-name">Período 1</span> '
            '{{ segmented.periodos[0].texto }}</summary>'
            '<details class="period-location"><summary>Localização no texto de trabalho</summary></details>'
            '</details></details>',
            encoding="utf-8",
        )
        application.jinja_loader = FileSystemLoader(str(template_directory))
        with sqlite3.connect(self.database) as connection:
            saved_before = tuple(connection.iterdump())

        old_page = self.client.get(location)
        self.assertEqual(old_page.status_code, 200)
        old_html = old_page.get_data(as_text=True)
        self.assertIn("Localização no texto de trabalho", old_html)
        self.assertIn("Maria chegou.", old_html)

        # Atualiza só o arquivo em disco, como um git pull com o servidor aberto.
        previous_mtime = template.stat().st_mtime_ns
        template.write_text(current_template, encoding="utf-8")
        changed_mtime = previous_mtime + 1_000_000_000
        os.utime(template, ns=(changed_mtime, changed_mtime))
        updated_page = self.client.get(location)
        self.assertEqual(updated_page.status_code, 200)
        updated_html = updated_page.get_data(as_text=True)
        self.assertNotIn("Localização no texto de trabalho", updated_html)
        self.assertNotIn('class="hierarchy-unit-name"', updated_html)
        self.assertIn('class="hierarchy-paragraph-body"', updated_html)
        self.assertIn('class="period-text hierarchy-period-text"', updated_html)
        self.assertIn("Maria chegou.", updated_html)
        self.assertIn("João saiu.", updated_html)
        self.assertIn("Ela voltou.", updated_html)
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(tuple(connection.iterdump()), saved_before)

    def test_empty_and_whitespace_are_not_saved(self):
        for content in ["", " \t\r\n", "\u2003"]:
            with self.subTest(content=repr(content)):
                response = self.client.post("/envios", data={"content": content})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.records(), [])

    def test_markup_is_saved_as_text_and_escaped_in_html(self):
        content = "</textarea><script>alert('texto')</script>"
        response = self.client.post("/envios", data={"content": content}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn(content, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(self.records()[0][1], content)

    def test_unknown_record_returns_404(self):
        self.assertEqual(self.client.get("/envios/999").status_code, 404)

    def test_oversized_request_is_not_saved(self):
        response = self.client.post("/envios", data={"content": "a" * (2 * 1024 * 1024)})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
