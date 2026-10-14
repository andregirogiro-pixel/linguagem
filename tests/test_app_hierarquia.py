"""Navegação progressiva, persistência e fronteiras HTTP da análise estrutural."""

import copy
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from app import create_app
from hierarquia_sintatica import ErroModelo


class ArvoreControlada:
    """Árvore de contrato: não substitui a avaliação do analisador real."""

    chamadas = 0

    def parse(self, tokens):
        type(self).chamadas += 1
        return {"rotulo": "ROOT", "filhos": [{"rotulo": "S", "filhos": [
            {"rotulo": "NP", "filhos": [{"texto": tokens[0]}]},
            {"rotulo": "VP", "filhos": [{"texto": t} for t in tokens[1:]]},
        ]}]}

    def provenance(self):
        return {"ferramenta": "adaptador_controlado", "versao": "1", "modelo": "teste"}


class HierarchyAppTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.database = Path(folder.name) / "textos.sqlite3"
        ArvoreControlada.chamadas = 0
        self.config = {"TESTING": True, "DATABASE": str(self.database),
                       "HIERARCHY_ADAPTER_FACTORY": ArvoreControlada}
        self.client = create_app(self.config).test_client()
        r = self.client.post('/envios', data={"content": "Maria chegou. João saiu.\n\nEla voltou.\n\nTodos partiram."})
        self.document_id = int(r.headers['Location'].rsplit('/', 1)[-1])
        self.base = f'/envios/{self.document_id}'
        r = self.client.post(self.base + '/segmentacoes', json={})
        self.assertEqual(r.status_code, 201)
        self.segmented = r.get_json()
        self.payload = {"segmentacao_id": self.segmented['segmentacao_id'],
                        "periodo_id": self.segmented['periodos'][0]['id']}

    def rows(self, table):
        assert table in {'submissions', 'preparations', 'segmentations', 'annotations', 'analyses', 'hierarchy_runs'}
        with sqlite3.connect(self.database) as connection:
            return connection.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()

    def run_hierarchy(self, **options):
        r = self.client.post(self.base + '/hierarquias', json={**self.payload, **options})
        self.assertIn(r.status_code, (200, 201), r.get_data(as_text=True))
        return r

    def test_page_is_read_only_and_supports_three_paragraphs(self):
        with patch('app.save_annotation', side_effect=AssertionError('GET executou análise')):
            r = self.client.get(self.base + '#etapa-segmentacao')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.rows('hierarchy_runs'), [])
        html = r.get_data(as_text=True)
        class Details(HTMLParser):
            def __init__(self):
                super().__init__()
                self.items = []

            def handle_starttag(self, tag, attrs):
                if tag == 'details':
                    self.items.append(dict(attrs))
        parser = Details()
        parser.feed(html)
        paragraphs = [a for a in parser.items if a.get('class') == 'hierarchy-paragraph']
        periods = [a for a in parser.items if 'data-hierarchy-period' in a]
        self.assertEqual(len(paragraphs), 3)
        self.assertEqual(len(periods), 4)
        self.assertTrue(all('open' not in a for a in paragraphs + periods))
        self.assertIn('Baixar segmentação JSON', html)

    def test_lazy_chain_preserves_source_and_reuses_saved_result(self):
        before = {t: self.rows(t) for t in ('submissions', 'preparations', 'segmentations')}
        result = self.run_hierarchy()
        self.assertEqual(result.status_code, 201)
        record = result.get_json()
        self.assertEqual(record['analise']['anotacao']['segmentacao'], self.segmented)
        self.assertEqual({t: self.rows(t) for t in before}, before)
        self.assertEqual(len(self.rows('annotations')), 1)
        self.assertEqual(len(self.rows('analyses')), 1)
        repeated = self.run_hierarchy()
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.get_json(), record)
        self.assertEqual(ArvoreControlada.chamadas, 1)

    def test_history_restart_and_download_without_model(self):
        original = self.run_hierarchy().get_json()
        newer = self.run_hierarchy(regenerar=True).get_json()
        self.assertNotEqual(original['hierarquia_id'], newer['hierarquia_id'])
        self.assertEqual(len(self.rows('hierarchy_runs')), 2)
        cfg = {**self.config, 'HIERARCHY_ADAPTER_FACTORY': lambda: self.fail('Modelo carregado na leitura')}
        client = create_app(cfg).test_client()
        r = client.get(self.base + '/hierarquia.json', query_string={'hierarquia_id': original['hierarquia_id']})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json(), original)
        r = client.get(self.base, query_string={'hierarquia_id': original['hierarquia_id']})
        self.assertEqual(r.status_code, 200)
        self.assertIn('Análise estrutural salva', r.get_data(as_text=True))

    def test_unavailable_model_rolls_back_new_upstream_records(self):
        with patch.object(ArvoreControlada, 'parse', side_effect=ErroModelo('Modelo não instalado')):
            r = self.client.post(self.base + '/hierarquias', json=self.payload)
        self.assertEqual(r.status_code, 503)
        for table in ('annotations', 'analyses', 'hierarchy_runs'):
            self.assertEqual(self.rows(table), [])

    def test_explicit_older_annotation_is_not_replaced_by_newer_one(self):
        first = self.client.post(self.base + '/anotacoes', json={
            'segmentacao_id': self.segmented['segmentacao_id'],
        }).get_json()
        newer = self.client.post(self.base + '/anotacoes', json={
            'segmentacao_id': self.segmented['segmentacao_id'],
        }).get_json()
        self.assertNotEqual(first['anotacao_id'], newer['anotacao_id'])
        record = self.run_hierarchy(anotacao_id=first['anotacao_id']).get_json()
        self.assertEqual(record['anotacao_id'], first['anotacao_id'])
        html = self.client.get(self.base, query_string={'anotacao_id': first['anotacao_id']}).get_data(as_text=True)
        self.assertIn('data-anotacao-id="' + first['anotacao_id'] + '"', html)
        r = self.client.post(self.base + '/hierarquias', json={
            **self.payload, 'anotacao_id': newer['anotacao_id'], 'analise_id': record['analise_id'],
        })
        self.assertEqual(r.status_code, 409)

    def test_invalid_inputs_and_cross_document_period_are_rejected(self):
        for change in ({'regenerar': 1}, {'periodo_id': None}, {'segmentacao_id': []}, {'ignorado': True}):
            r = self.client.post(self.base + '/hierarquias', json={**self.payload, **change})
            self.assertEqual(r.status_code, 400)
        r = self.client.post(self.base + '/hierarquias', json={**self.payload, 'periodo_id': 'outro'})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.rows('annotations'), [])
        self.assertEqual(self.rows('hierarchy_runs'), [])

    def test_corrupted_saved_tree_is_not_exported(self):
        result = self.run_hierarchy().get_json()
        corrupt = copy.deepcopy(result)
        corrupt['arvore']['token_ids'] = []
        with sqlite3.connect(self.database) as connection:
            connection.execute('UPDATE hierarchy_runs SET record_json = ?', (json.dumps(corrupt),))
        r = self.client.get(self.base + '/hierarquia.json', query_string={'hierarquia_id': result['hierarquia_id']})
        self.assertEqual(r.status_code, 409)

    def test_no_javascript_form_redirects_to_saved_hierarchy(self):
        r = self.client.post(self.base + '/hierarquias', data=self.payload)
        self.assertEqual(r.status_code, 303)
        self.assertIn('hierarquia_id=', r.headers['Location'])
        self.assertTrue(r.headers['Location'].endswith('#etapa-segmentacao'))
        self.assertEqual(self.client.get(r.headers['Location']).status_code, 200)

    def test_old_selected_version_remains_readable_when_newer_tree_is_corrupt(self):
        original = self.run_hierarchy().get_json()
        newer = self.run_hierarchy(regenerar=True).get_json()
        newer['arvore']['token_ids'] = []
        with sqlite3.connect(self.database) as connection:
            connection.execute('UPDATE hierarchy_runs SET record_json = ? WHERE hierarchy_id = ?',
                               (json.dumps(newer), newer['hierarquia_id']))
        r = self.client.get(self.base, query_string={'hierarquia_id': original['hierarquia_id']})
        self.assertEqual(r.status_code, 200)
        r = self.client.get(self.base, query_string={'hierarquia_id': newer['hierarquia_id']})
        self.assertEqual(r.status_code, 409)

    def test_cold_inference_does_not_hold_database_write_lock(self):
        started, release = Event(), Event()

        class Blocking(ArvoreControlada):
            def parse(self, tokens):
                started.set()
                if not release.wait(10):
                    raise AssertionError('Inferência de teste não foi liberada')
                return super().parse(tokens)

        app = self.client.application
        app.config['HIERARCHY_ADAPTER_FACTORY'] = Blocking

        def request_hierarchy(period_id):
            with app.test_client() as client:
                return client.post(self.base + '/hierarquias', json={
                    **self.payload, 'periodo_id': period_id,
                }).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(request_hierarchy, self.payload['periodo_id'])
            try:
                self.assertTrue(started.wait(5))
                second = pool.submit(request_hierarchy, self.segmented['periodos'][1]['id'])
                # Uma gravação independente deve terminar enquanto o modelo
                # ainda está bloqueado pelo evento, sem aguardar seu término.
                r = self.client.post('/envios', data={'content': 'Outro documento.'})
                self.assertEqual(r.status_code, 303)
                self.assertEqual(self.rows('annotations'), [])
            finally:
                release.set()
            self.assertEqual(first.result(timeout=10), 201)
            self.assertEqual(second.result(timeout=10), 201)
        self.assertEqual(len(self.rows('annotations')), 1)
        self.assertEqual(len(self.rows('analyses')), 1)
        self.assertEqual(len(self.rows('hierarchy_runs')), 2)


if __name__ == '__main__':
    unittest.main()
