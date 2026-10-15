import csv
import io
import json
import unittest

from gerador.app.exporter import exportar_csv, exportar_jsonl
from gerador.app.validator import contar_frases, validar_paragrafo


class ValidacaoExportacaoTests(unittest.TestCase):
    def test_faixas_do_yaml_e_um_unico_paragrafo(self):
        self.assertTrue(validar_paragrafo("A chuva parou. O jardim floresceu.", {"tamanho": "curto"})["aceito"])
        self.assertTrue(validar_paragrafo("A chuva parou.", {"tamanho": "curto"})["aceito"])
        self.assertTrue(validar_paragrafo("Uma frase. " * 7, {"tamanho": "longo"})["aceito"])
        self.assertFalse(validar_paragrafo("Uma frase. " * 12, {"tamanho": "longo"})["aceito"])
        self.assertFalse(validar_paragrafo("Primeiro bloco.\n\nSegundo bloco.", {"tamanho": "curto"})["aceito"])

    def test_abreviacao_decimal_aspas_e_interrogacao(self):
        self.assertEqual(contar_frases('O Dr. Silva pagou 2.50 euros. "Valeu a pena?"'), 2)
        self.assertEqual(contar_frases("A. Silva chegou cedo. Encontrou a porta aberta."), 2)

    def test_idioma_truncamento_e_comentario_externo(self):
        for texto in (
            "The train arrived at the station. Everyone hurried to find their seats.",
            "Um trem chegou à estação e os passageiros ainda estavam procurando",
            "Aqui está o parágrafo: O trem chegou à estação. Todos encontraram seus lugares.",
            '"O trem chegou à estação. Todos encontraram seus lugares."',
        ):
            with self.subTest(texto=texto):
                self.assertFalse(validar_paragrafo(texto, {"tamanho": "curto"})["aceito"])

    def test_exportacoes_preservam_acentos_aspas_virgulas_e_prompt_multilinha(self):
        lote = {
            "id": 12, "modelo": "empresa/modelo", "parametros_solicitados": {"tom": "variar"},
            "itens": [{
                "id": 34, "indice": 1, "status": "rejeitado", "parametros": {"tom": "serio"},
                "prompt": 'Um prompt com "aspas", vírgulas\ne nova linha.',
                "texto": 'A ação terminou, "enfim". Houve silêncio.',
                "metadados": {"uso": {"total_tokens": 42}}, "erro": "abertura_repetida", "temperatura": 0.8,
            }],
        }
        jsonl = [json.loads(linha) for linha in exportar_jsonl(lote).splitlines()]
        self.assertEqual(jsonl[0]["item"], lote["itens"][0])
        self.assertEqual(jsonl[0]["lote"]["parametros_solicitados"], {"tom": "variar"})
        linhas = list(csv.DictReader(io.StringIO(exportar_csv(lote).lstrip("\ufeff"))))
        self.assertEqual(len(linhas), 1)
        for campo in ("prompt", "texto", "status", "erro"):
            self.assertEqual(linhas[0][campo], lote["itens"][0][campo])
        self.assertEqual(json.loads(linhas[0]["metadados"]), lote["itens"][0]["metadados"])


if __name__ == "__main__":
    unittest.main()
