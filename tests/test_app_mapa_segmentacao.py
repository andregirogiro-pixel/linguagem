"""Mapa no Flask: mesma cadeia histórica, HTML seguro e leitura sem gravação."""

import copy
from html.parser import HTMLParser
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import app as app_module
from app import create_app
from fixtures_regras import INSTANTE, construir_analise


class _ArvoreHTML(HTMLParser):
    """Lê apenas identificadores e relações de continência, sem exigir layout."""

    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.clauses = []
        self.periods = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        scope = dict(self.stack[-1][1]) if self.stack else {}
        for field in ("paragraph", "period", "clause"):
            value = attrs.get(f"data-{field}-id")
            if value is not None:
                scope[field] = value
                if field == "clause":
                    self.clauses.append(dict(scope))
                elif field == "period":
                    self.periods.append(dict(scope))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, scope))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class MapaSegmentacaoIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "mapa.sqlite3"
        self.client = create_app({"TESTING": True, "DATABASE": str(self.database)}).test_client()

    def submit(self, texto="Ela quer sair."):
        response = self.client.post("/envios", data={"content": texto})
        self.assertEqual(response.status_code, 303)
        document_id = int(response.headers["Location"].rsplit("/", 1)[-1])
        prepared = self.client.get(f"/envios/{document_id}/preparacao.json").get_json()
        return document_id, prepared

    def stage(self, document_id, etapa, **opcoes):
        response = self.client.post(f"/envios/{document_id}/{etapa}", json=opcoes)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()

    def chain(self, document_id, prepared, *, segmentation_id="segmentacao-mapa", linhas=None):
        segmented = self.stage(document_id, "segmentacoes", preparacao_id=prepared["preparacao_id"],
                               segmentacao_id=segmentation_id)
        with patch("fixtures_regras.preparar_texto", return_value=prepared), \
             patch("fixtures_regras.segmentar_preparacao", return_value=segmented):
            analyzed = construir_analise("desejo", linhas=linhas)
        with patch("app.anotar_segmentacao", return_value=copy.deepcopy(analyzed["anotacao"])):
            annotated = self.stage(document_id, "anotacoes", segmentacao_id=segmentation_id)
        with patch("app.analisar_sintaxe_entidades", return_value=copy.deepcopy(analyzed)):
            self.stage(document_id, "analises", anotacao_id=annotated["anotacao_id"])
        return self.stage(document_id, "regras", analise_id=analyzed["analise_id"],
                          execucao_id=f"regras-{segmentation_id}", registrado_em=INSTANTE,
                          regras_habilitadas=["oracao_predicado"])

    def snapshot(self):
        with sqlite3.connect(self.database) as connection:
            return "\n".join(connection.iterdump())

    def exports(self, document_id, ruled):
        options = (("preparacao.json", "preparacao_id"), ("segmentacao.json", "segmentacao_id"),
                   ("anotacao.json", "anotacao_id"), ("analise.json", "analise_id"),
                   ("regras.json", "execucao_id"))
        exported = {}
        for path, field in options:
            response = self.client.get(f"/envios/{document_id}/{path}", query_string={field: ruled[field]})
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            exported[path] = response.get_data()
        return exported

    def test_get_exibe_mesma_cadeia_sem_reprocessar_nem_alterar_banco_ou_exports(self):
        document_id, prepared = self.submit()
        ruled = self.chain(document_id, prepared)
        antes = self.snapshot()
        exports = self.exports(document_id, ruled)
        with patch("app.anotar_segmentacao", side_effect=AssertionError("reanotação proibida")), \
             patch("app.analisar_sintaxe_entidades", side_effect=AssertionError("reanálise proibida")), \
             patch("app.aplicar_regras_linguisticas", side_effect=AssertionError("reexecução proibida")), \
             patch("app.render_template", wraps=app_module.render_template) as render:
            response = self.client.get(f"/envios/{document_id}", query_string={"execucao_id": ruled["execucao_id"]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        mapa = render.call_args.kwargs["segmentation_view"]
        self.assertEqual(mapa["clauses_total"], 2)
        arvore = _ArvoreHTML(response.get_data(as_text=True))
        self.assertEqual(len(arvore.clauses), 2)
        for elemento, ocorrencia in zip(arvore.clauses, ruled["ocorrencias"]):
            self.assertEqual(elemento, {"paragraph": ocorrencia["paragrafo_id"],
                                        "period": ocorrencia["periodo_id"], "clause": ocorrencia["id"]})
        self.assertEqual(self.snapshot(), antes)
        self.assertEqual(self.exports(document_id, ruled), exports)

    def test_selecao_historica_nao_mistura_oracoes_com_segmentacao_mais_recente(self):
        document_id, prepared = self.submit()
        ruled = self.chain(document_id, prepared, segmentation_id="segmentacao-antiga")
        nova = self.stage(document_id, "segmentacoes", preparacao_id=prepared["preparacao_id"],
                          segmentacao_id="segmentacao-nova")
        atual = self.client.get(f"/envios/{document_id}")
        self.assertEqual(atual.status_code, 200)
        arvore_atual = _ArvoreHTML(atual.get_data(as_text=True))
        self.assertEqual(arvore_atual.clauses, [])
        self.assertEqual([p["period"] for p in arvore_atual.periods], [p["id"] for p in nova["periodos"]])
        historica = self.client.get(f"/envios/{document_id}", query_string={"execucao_id": ruled["execucao_id"]})
        self.assertEqual(historica.status_code, 200)
        arvore_antiga = _ArvoreHTML(historica.get_data(as_text=True))
        self.assertEqual([c["clause"] for c in arvore_antiga.clauses], [o["id"] for o in ruled["ocorrencias"]])
        self.assertTrue(all(c["period"].startswith("segmentacao-antiga:") for c in arvore_antiga.clauses))
        conflito = self.client.get(f"/envios/{document_id}", query_string={
            "execucao_id": ruled["execucao_id"], "segmentacao_id": nova["segmentacao_id"],
        })
        self.assertEqual(conflito.status_code, 400)

    def test_texto_literal_das_oracoes_e_anotacoes_e_escapado_no_html(self):
        document_id, prepared = self.submit('Ela quer "<script>".')
        # Referência manual: todos os tokens pertencem ao predicado "quer".
        linhas = [("Ela", "ela", "PRON", 1, "nsubj", {}),
                  ("quer", "querer", "VERB", 1, "ROOT", {"VerbForm": "Fin"}),
                  ('"', '"', "PUNCT", 1, "punct", {}),
                  ("<", "<", "PUNCT", 1, "punct", {}),
                  ("script", "script", "NOUN", 1, "obj", {}),
                  (">", ">", "PUNCT", 1, "punct", {}),
                  ('"', '"', "PUNCT", 1, "punct", {}),
                  (".", ".", "PUNCT", 1, "punct", {})]
        ruled = self.chain(document_id, prepared, linhas=linhas)
        response = self.client.get(f"/envios/{document_id}", query_string={"execucao_id": ruled["execucao_id"]})
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertEqual(len(_ArvoreHTML(html).clauses), 1)


if __name__ == "__main__":
    unittest.main()
