"""Regressão do período inspecionado no PDF do usuário.

O spaCy real fornece as previsões de origem; a árvore controlada representa
os encaixes do PDF para testar a projeção, sem simular avaliação do Stanza.
"""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from anotacao import anotar_segmentacao
from hierarquia_sintatica import construir_hierarquia, validar_hierarquia
from preparacao import preparar_texto
from revisao_hierarquia import preparar_revisao
from segmentacao import segmentar_preparacao
from sintaxe_entidades import analisar_sintaxe_entidades


TEXTO = 'Quando cheguei à biblioteca, percebi que havia esquecido o caderno em casa.'


def no(rotulo, *filhos):
    return {'rotulo': rotulo, 'filhos': list(filhos)}


def palavra(rotulo, texto):
    return no(rotulo, {'texto': texto})


class ArvoreDoPeriodo:
    def parse(self, tokens):
        biblioteca = no('PP', palavra('DET', 'à'), no('NP', palavra('NOUN', 'biblioteca')))
        quando = no('CONJP', no('CONJP', palavra('SCONJ', 'Quando'),
                    no('S', no('VP', palavra('VERB', 'cheguei'), biblioteca))), palavra('PUNCT', ','))
        caderno = no('NP', palavra('DET', 'o'), palavra('NOUN', 'caderno'))
        completiva = no('CP', palavra('SCONJ', 'que'), no('S',
                        no('VP', palavra('AUX', 'havia'), palavra('VERB', 'esquecido')), caderno))
        casa = no('PP', palavra('ADP', 'em'), no('NP', palavra('NOUN', 'casa')))
        return no('ROOT', no('S', no('S', no('VP', quando,
                  no('VP', no('VP', palavra('VERB', 'percebi'), completiva), casa))), palavra('PUNCT', '.')))

    def provenance(self):
        return {'ferramenta': 'arvore_controlada_regressao_pdf', 'versao': '1'}


class RevisaoPeriodoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.segmentacao = segmentar_preparacao(preparar_texto(TEXTO, documento_id='periodo-revisado'))
        cls.analise = analisar_sintaxe_entidades(anotar_segmentacao(cls.segmentacao))
        cls.hierarquia = construir_hierarquia(cls.analise, cls.segmentacao['periodos'][0]['id'], adaptador=ArvoreDoPeriodo())

    def test_erros_morfologicos_sao_revisados_sem_apagar_previsoes(self):
        original = deepcopy(self.hierarquia)
        vista = preparar_revisao(self.hierarquia)
        words = {p['texto']: p for p in vista['palavras']}
        self.assertEqual(words['cheguei']['revisao']['lema'], 'chegar')
        self.assertEqual(words['percebi']['revisao']['lema'], 'perceber')
        self.assertEqual(words['percebi']['revisao']['morfologia'], {
            'Mood': 'Ind', 'Number': 'Sing', 'Person': '1', 'Tense': 'Past', 'VerbForm': 'Fin',
        })
        self.assertTrue(words['percebi']['revisao']['justificativas'])
        self.assertEqual(self.hierarquia, original)
        self.assertTrue(validar_hierarquia(self.hierarquia)['pronto'])
        for revised, raw in zip(vista['palavras'], original['palavras']):
            for field in raw:
                self.assertEqual(revised[field], raw[field])

    def test_funcoes_e_subtipos_correspondem_aos_niveis_linguisticos(self):
        vista = preparar_revisao(self.hierarquia)
        words = {p['texto']: p for p in vista['palavras']}
        self.assertEqual(words['Quando']['revisao']['pos'], 'SCONJ')
        self.assertIn('Núcleo da oração', words['cheguei']['revisao']['funcao_sintatica'])
        self.assertIn('Núcleo da oração', words['esquecido']['revisao']['funcao_sintatica'])
        self.assertNotIn('objeto', words['biblioteca']['revisao']['funcao_sintatica'])
        self.assertEqual(words['biblioteca']['dependencia'], 'obj')
        self.assertTrue(words['casa']['alertas_revisao'])
        self.assertEqual(len(vista['oracoes']), 3)
        self.assertEqual(vista['oracoes'][0]['classificacao'], 'Oração subordinada adverbial temporal')
        self.assertEqual(vista['oracoes'][1]['rotulo'], 'Oração principal')
        self.assertEqual(vista['oracoes'][2]['classificacao'], 'Oração subordinada substantiva objetiva direta')
        self.assertEqual(vista['oracoes'][2]['tempo_composto'], 'Pretérito mais-que-perfeito composto do indicativo')

    def test_principal_legivel_e_florestas_nao_repetem_arvore_inteira(self):
        vista = preparar_revisao(self.hierarquia)
        principal = vista['oracoes'][1]
        self.assertEqual([t['texto'] for t in principal['trechos_leitura']], ['percebi'])

        def folhas(raizes):
            for n in raizes:
                if n['tipo'] == 'palavra':
                    yield n['token_id']
                yield from folhas(n['filhos'])

        for oracao in vista['oracoes']:
            self.assertTrue(set(folhas(oracao['raizes'])) <= set(oracao['token_ids']))
        self.assertEqual(vista['arvore_tecnica'], self.hierarquia['arvore'])

    def test_api_e_download_revisados_preservam_registro_armazenado(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder)/'teste.sqlite3'
            app = create_app({'TESTING': True, 'DATABASE': str(database), 'HIERARCHY_ADAPTER_FACTORY': ArvoreDoPeriodo})
            client = app.test_client()
            response = client.post('/envios', data={'content': TEXTO})
            base = response.headers['Location']
            segmented = client.post(base+'/segmentacoes', json={}).get_json()
            payload = {'segmentacao_id': segmented['segmentacao_id'], 'periodo_id': segmented['periodos'][0]['id'],
                       'incluir_visualizacao': True}
            response = client.post(base+'/hierarquias', json=payload)
            self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
            envelope = response.get_json()
            raw = envelope['hierarquia']
            with sqlite3.connect(database) as connection:
                before = connection.execute('SELECT record_json FROM hierarchy_runs').fetchone()[0]
            self.assertEqual(json.loads(before), raw)
            with patch('app.construir_hierarquia', side_effect=AssertionError('Leitura executou modelo')):
                revised = client.get(base+'/hierarquia.json', query_string={'hierarquia_id': raw['hierarquia_id'], 'visualizacao': '1'})
                original = client.get(base+'/hierarquia.json', query_string={'hierarquia_id': raw['hierarquia_id']})
                page = client.get(base, query_string={'hierarquia_id': raw['hierarquia_id']})
            self.assertEqual(revised.get_json(), envelope)
            self.assertEqual(original.get_json(), raw)
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True)
            self.assertIn('imperfeito', html)
            self.assertNotIn('Tempo: imperativo', html)
            self.assertEqual(html.count('Árvore completa e dados do modelo'), 1)
            with sqlite3.connect(database) as connection:
                after = connection.execute('SELECT record_json FROM hierarchy_runs').fetchone()[0]
            self.assertEqual(after, before)
            self.assertEqual(client.post(base+'/hierarquias', json=payload).status_code, 200)


if __name__ == '__main__':
    unittest.main()
