"""Integração do protótipo no HTML e nos registros reais do Flask.

A árvore controlada reproduz o período revisado pelo usuário; spaCy e as
etapas HTTP/persistência são reais. Estes testes verificam apresentação e
rastreabilidade, sem avaliar a qualidade preditiva do Stanza.
"""

from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app import create_app
from test_app_hierarquia import ArvoreControlada
from test_apresentacao_segmentacao import PRIMEIRO_PARAGRAFO
from test_revisao_periodo import ArvoreDoPeriodo


class Elemento:
    def __init__(self, tag, atributos=(), pai=None):
        self.tag = tag
        self.atributos = dict(atributos)
        self.pai = pai
        self.conteudo = []

    def classe(self, nome):
        return nome in self.atributos.get("class", "").split()

    def filhos(self):
        return [item for item in self.conteudo if isinstance(item, Elemento)]

    def buscar(self, condicao):
        encontrados = []
        for filho in self.filhos():
            if condicao(filho):
                encontrados.append(filho)
            encontrados.extend(filho.buscar(condicao))
        return encontrados

    def texto(self):
        return "".join(item.texto() if isinstance(item, Elemento) else item for item in self.conteudo)

    def rotulo(self):
        return " ".join(self.texto().split())


class Documento(HTMLParser):
    _VAZIOS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.raiz = Elemento("documento")
        self.pilha = [self.raiz]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        elemento = Elemento(tag, attrs, self.pilha[-1])
        self.pilha[-1].conteudo.append(elemento)
        if tag not in self._VAZIOS:
            self.pilha.append(elemento)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VAZIOS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for indice in range(len(self.pilha) - 1, 0, -1):
            if self.pilha[indice].tag == tag:
                del self.pilha[indice:]
                return

    def handle_data(self, data):
        self.pilha[-1].conteudo.append(data)


class SegmentacaoPrototipoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pasta = tempfile.TemporaryDirectory()
        cls.addClassCleanup(pasta.cleanup)
        cls.database = Path(pasta.name) / "prototipo.sqlite3"
        cls.app = create_app({"TESTING": True, "DATABASE": str(cls.database),
                              "HIERARCHY_ADAPTER_FACTORY": ArvoreDoPeriodo})
        cls.client = cls.app.test_client()
        cls.texto = PRIMEIRO_PARAGRAFO + "\n\nDepois, voltei para casa."
        resposta = cls.client.post("/envios", data={"content": cls.texto})
        cls.base = resposta.headers["Location"]
        resposta = cls.client.post(cls.base + "/segmentacoes", json={})
        if resposta.status_code != 201:
            raise AssertionError(resposta.get_data(as_text=True))
        cls.segmentacao = resposta.get_json()
        cls.payload = {"segmentacao_id": cls.segmentacao["segmentacao_id"],
                       "periodo_id": cls.segmentacao["periodos"][0]["id"], "incluir_visualizacao": True}
        resposta = cls.client.post(cls.base + "/hierarquias", json=cls.payload)
        if resposta.status_code != 201:
            raise AssertionError(resposta.get_data(as_text=True))
        cls.envelope = resposta.get_json()
        cls.raw = cls.envelope["hierarquia"]

    def pagina(self):
        with patch("app.construir_hierarquia", side_effect=AssertionError("GET executou modelo")), \
             patch("app.anotar_segmentacao", side_effect=AssertionError("GET reanotou o texto")):
            resposta = self.client.get(self.base, query_string={"hierarquia_id": self.raw["hierarquia_id"]})
        self.assertEqual(resposta.status_code, 200, resposta.get_data(as_text=True))
        return Documento(resposta.get_data(as_text=True)).raiz

    def unico(self, raiz, condicao):
        encontrados = raiz.buscar(condicao)
        self.assertEqual(len(encontrados), 1)
        return encontrados[0]

    def periodo(self, pagina):
        return self.unico(pagina, lambda e: e.classe("hierarchy-period")
                          and e.atributos.get("data-periodo-id") == self.payload["periodo_id"])

    def registros(self):
        with sqlite3.connect(self.database) as conexao:
            return conexao.execute("SELECT record_json FROM hierarchy_runs ORDER BY id").fetchall()

    def test_oracoes_planas_em_ordem_com_sintaxe_e_morfologia_irmas(self):
        periodo = self.periodo(self.pagina())
        oracoes = periodo.buscar(lambda e: e.classe("hierarchy-clause"))
        self.assertEqual(len(oracoes), 3)
        self.assertEqual([o.atributos["data-oracao-id"] for o in oracoes],
                         [o["id"] for o in self.envelope["visualizacao"]["oracoes"]])
        self.assertEqual([[f.rotulo() for f in o.filhos()[0].filhos()] for o in oracoes], [
            ["Oração 1", "Quando cheguei à biblioteca"], ["Oração 2", "percebi"],
            ["Oração 3", "que havia esquecido o caderno em casa"]])
        listas = {id(o.pai.pai) for o in oracoes}
        self.assertEqual(len(listas), 1)
        self.assertTrue(oracoes[0].pai.pai.classe("hierarchy-clauses"))
        for oracao in oracoes:
            self.assertEqual(oracao.buscar(lambda e: e.classe("hierarchy-clause")), [])
            paineis = self.unico(oracao, lambda e: e.classe("hierarchy-clause-panels"))
            caixas = [f for f in paineis.filhos() if f.tag == "details"]
            self.assertEqual([f.atributos["class"] for f in caixas], ["hierarchy-syntax", "hierarchy-morphology"])
            self.assertEqual([f.filhos()[0].rotulo() for f in caixas], ["Análise sintática", "Análise morfológica"])
            self.assertTrue(all("open" not in f.atributos for f in caixas))

    def test_contagens_e_sujeito_inferido_nao_modificam_o_texto(self):
        periodo = self.periodo(self.pagina())
        for atributo, esperado in (("data-hierarchy-word-count", "12"),
                                   ("data-hierarchy-character-count", "75"),
                                   ("data-hierarchy-clause-count", "3")):
            estatistica = self.unico(periodo, lambda e: e.tag == "dd" and atributo in e.atributos)
            self.assertEqual(estatistica.rotulo(), esperado)
        oracoes = periodo.buscar(lambda e: e.classe("hierarchy-clause"))
        sujeitos = []
        for oracao in oracoes:
            sintaxe = self.unico(oracao, lambda e: e.classe("hierarchy-syntax"))
            sujeitos.append(len(sintaxe.buscar(lambda e: e.classe("hierarchy-analysis-text") and e.texto() == "(eu)")))
            morfologia = self.unico(oracao, lambda e: e.classe("hierarchy-morphology"))
            self.assertNotIn("(eu)", morfologia.texto())
        self.assertEqual(sujeitos, [1, 1, 0])
        self.assertEqual(len(periodo.buscar(lambda e: e.classe("hierarchy-morphology-word"))), 12)
        texto = self.unico(periodo, lambda e: e.classe("hierarchy-period-text"))
        self.assertEqual(texto.texto(), self.segmentacao["periodos"][0]["texto"])

    def test_localizacao_fica_apos_toda_a_analise_do_periodo(self):
        periodo = self.periodo(self.pagina())
        corpo = self.unico(periodo, lambda e: e.classe("hierarchy-period-body"))
        localizacao = self.unico(corpo, lambda e: e.classe("period-location"))
        self.assertIs(localizacao.pai, corpo)
        self.assertIs(corpo.filhos()[-1], localizacao)
        self.assertEqual(localizacao.filhos()[0].rotulo(), "Localização no texto de trabalho")
        self.assertEqual(len(corpo.buscar(lambda e: e.classe("hierarchy-complete"))), 1)

    def test_paragrafos_destaques_e_contagem_parcial_preservam_o_literal(self):
        pagina = self.pagina()
        paragrafos = pagina.buscar(lambda e: e.classe("hierarchy-paragraph"))
        self.assertEqual(len(paragrafos), 2)
        for elemento, original in zip(paragrafos, self.segmentacao["paragrafos"]):
            texto = self.unico(elemento, lambda e: e.classe("hierarchy-paragraph-text"))
            self.assertEqual(texto.texto(), original["texto"])
            destaques = texto.buscar(lambda e: e.tag == "mark")
            periodos = [p for p in self.segmentacao["periodos"] if p["paragrafo_id"] == original["id"]]
            self.assertEqual([d.texto() for d in destaques], [p["texto"] for p in periodos])
            self.assertEqual([d.atributos["data-periodo-id"] for d in destaques], [p["id"] for p in periodos])
        parcial = self.unico(paragrafos[0], lambda e: "data-hierarchy-paragraph-clause-count" in e.atributos)
        progresso = self.unico(paragrafos[0], lambda e: "data-hierarchy-paragraph-analyzed-count" in e.atributos)
        self.assertEqual(parcial.rotulo(), "3 identificadas")
        self.assertEqual(progresso.rotulo(), "1 de 4 períodos analisados")
        pendente = self.unico(paragrafos[1], lambda e: "data-hierarchy-paragraph-clause-count" in e.atributos)
        self.assertEqual(pendente.rotulo(), "A identificar")
        stats = self.unico(paragrafos[0], lambda e: e.classe("hierarchy-paragraph-stats"))
        valores = {d.filhos()[0].rotulo(): d.filhos()[1].rotulo() for d in stats.filhos()}
        self.assertEqual(valores["Palavras"], "54")
        self.assertEqual(valores["Caracteres"], "341")

    def test_envelope_e_reabertura_preservam_json_original_e_armazenamento(self):
        self.assertEqual(set(self.envelope), {"hierarquia", "visualizacao"})
        self.assertEqual(self.raw["analise"]["anotacao"]["segmentacao"], self.segmentacao)
        self.assertTrue(all("termos" not in o and "morfologia" not in o for o in self.raw["oracoes"]))
        self.assertTrue(all("termos" in o and "morfologia" in o for o in self.envelope["visualizacao"]["oracoes"]))
        antes = self.registros()
        self.assertEqual(len(antes), 1)
        self.assertEqual(json.loads(antes[0][0]), self.raw)
        with patch("app.construir_hierarquia", side_effect=AssertionError("Leitura executou modelo")):
            self.pagina()
            query = {"hierarquia_id": self.raw["hierarquia_id"]}
            original = self.client.get(self.base + "/hierarquia.json", query_string=query)
            revisada = self.client.get(self.base + "/hierarquia.json", query_string={**query, "visualizacao": "1"})
            repetida = self.client.post(self.base + "/hierarquias", json=self.payload)
        self.assertEqual(original.status_code, 200)
        self.assertEqual(original.get_json(), self.raw)
        self.assertEqual(revisada.get_json(), self.envelope)
        self.assertEqual(repetida.status_code, 200)
        self.assertEqual(repetida.get_json(), self.envelope)
        self.assertEqual(self.registros(), antes)

    def test_periodo_nominal_mostra_morfologia_sem_repetir_floresta_primaria(self):
        with tempfile.TemporaryDirectory() as pasta:
            database = Path(pasta) / "nominal.sqlite3"
            app = create_app({"TESTING": True, "DATABASE": str(database),
                              "HIERARCHY_ADAPTER_FACTORY": ArvoreControlada})
            client = app.test_client()
            resposta = client.post("/envios", data={"content": "Silêncio."})
            base = resposta.headers["Location"]
            segmentacao = client.post(base + "/segmentacoes", json={}).get_json()
            resposta = client.post(base + "/hierarquias", json={
                "segmentacao_id": segmentacao["segmentacao_id"],
                "periodo_id": segmentacao["periodos"][0]["id"], "incluir_visualizacao": True})
            self.assertEqual(resposta.status_code, 201, resposta.get_data(as_text=True))
            envelope = resposta.get_json()
            self.assertEqual(envelope["hierarquia"]["oracoes"], [])
            self.assertEqual(envelope["visualizacao"]["oracoes"], [])
            self.assertEqual([linha["texto"] for linha in envelope["visualizacao"]["morfologia_periodo"]], ["Silêncio"])
            self.assertNotIn("morfologia_periodo", envelope["hierarquia"])
            with sqlite3.connect(database) as conexao:
                antes = conexao.execute("SELECT record_json FROM hierarchy_runs").fetchone()[0]
            query = {"hierarquia_id": envelope["hierarquia"]["hierarquia_id"]}
            with patch("app.construir_hierarquia", side_effect=AssertionError("GET executou modelo nominal")):
                resposta = client.get(base, query_string=query)
                revisada = client.get(base + "/hierarquia.json", query_string={**query, "visualizacao": "1"})
            self.assertEqual(resposta.status_code, 200)
            pagina = Documento(resposta.get_data(as_text=True)).raiz
            resultado = self.unico(pagina, lambda e: "data-hierarchy-result" in e.atributos)
            self.assertEqual(resultado.buscar(lambda e: e.classe("hierarchy-clause")), [])
            morfologia = self.unico(resultado, lambda e: e.classe("hierarchy-morphology"))
            self.assertIs(morfologia.pai, resultado)
            linhas = morfologia.buscar(lambda e: e.classe("hierarchy-morphology-word"))
            self.assertEqual(len(linhas), 1)
            self.assertEqual(self.unico(linhas[0], lambda e: e.classe("hierarchy-analysis-text")).texto(), "Silêncio")
            self.assertEqual(resultado.buscar(lambda e: e.classe("hierarchy-local-tree")), [])
            self.assertEqual(len(resultado.buscar(lambda e: e.classe("hierarchy-complete"))), 1)
            contagem = self.unico(pagina, lambda e: e.tag == "dd" and "data-hierarchy-clause-count" in e.atributos)
            self.assertEqual(contagem.rotulo(), "0")
            self.assertEqual(revisada.get_json(), envelope)
            with sqlite3.connect(database) as conexao:
                depois = conexao.execute("SELECT record_json FROM hierarchy_runs").fetchone()[0]
            self.assertEqual(depois, antes)
            self.assertEqual(json.loads(depois), envelope["hierarquia"])


if __name__ == "__main__":
    unittest.main()
