"""Integridade/offline do adaptador; a avaliação real é explícita e opcional.

As árvores artificiais abaixo exercitam o contrato de software, sem simular
validação linguística. Para avaliar os pesos reais já baixados:
LINGUA_TESTE_CONSTITUINTES_REAL=1 python -m unittest discover -s tests \
    -p test_modelo_constituintes.py
"""

import hashlib
import io
import os
from pathlib import Path
import ssl
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

import modelo_constituintes as modulo
from modelo_constituintes import (
    AlinhamentoConstituintesError,
    ModeloConstituintes,
    ModeloConstituintesIndisponivel,
)


class NoControlado:
    def __init__(self, label, children=()):
        self.label = label
        self.children = list(children)

    def is_leaf(self):
        return not self.children

    def leaf_labels(self):
        if self.is_leaf():
            return [self.label]
        return [texto for filho in self.children for texto in filho.leaf_labels()]


def arvore_controlada(tokens):
    return NoControlado("ROOT", [NoControlado("S", [
        NoControlado("X", [NoControlado(token)]) for token in tokens
    ])])


def folhas(no):
    if "texto" in no:
        return [no["texto"]]
    return [texto for filho in no["filhos"] for texto in folhas(filho)]


class AdaptadorConstituintesTests(unittest.TestCase):
    def setUp(self):
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.pasta = Path(pasta.name)
        self.modelo = ModeloConstituintes(self.pasta / "stanza")

    def test_pesos_ausentes_falham_offline_sem_importacao_ou_download(self):
        with patch.object(modulo, "_versao_instalada", return_value=modulo.STANZA_VERSION), \
                patch("urllib.request.urlopen") as rede:
            with self.assertRaisesRegex(ModeloConstituintesIndisponivel, "não foram preparados"):
                self.modelo.parse(["Maria", "chegou", "."])
            self.assertFalse(self.modelo.disponivel())
            self.assertFalse(self.modelo.provenance()["disponivel"])
        rede.assert_not_called()
        self.assertFalse(self.modelo.model_dir.exists())

    def test_versao_incompativel_e_rejeitada_antes_de_ler_pesos(self):
        with patch.object(modulo, "_versao_instalada", return_value="0.0.0"), \
                patch.object(self.modelo, "_artefatos") as artefatos:
            with self.assertRaisesRegex(ModeloConstituintesIndisponivel, "Stanza 1.11.0"):
                self.modelo.parse(["Oi", "."])
        artefatos.assert_not_called()

    def _instalacao_controlada(self):
        dados = b"pesos artificiais usados apenas no contrato de integridade"
        registro = b'{"registro":"artificial"}'
        md5 = hashlib.md5(dados, usedforsecurity=False).hexdigest()
        self.modelo.model_dir.mkdir()
        (self.modelo.model_dir / "resources.json").write_bytes(registro)
        modelos = {nome: md5 for nome in modulo.MODEL_MD5}
        for nome in modelos:
            destino = self.modelo.model_dir / "pt" / nome
            destino.parent.mkdir(parents=True, exist_ok=True)
            destino.write_bytes(dados)
        patch_md5 = patch.object(modulo, "MODEL_MD5", modelos)
        patch_sha = patch.object(modulo, "RESOURCES_SHA256", hashlib.sha256(registro).hexdigest())
        patch_versao = patch.object(modulo, "_versao_instalada", return_value=modulo.STANZA_VERSION)
        for objeto in (patch_md5, patch_sha, patch_versao):
            objeto.start()
            self.addCleanup(objeto.stop)

    def test_artefato_adulterado_e_rejeitado_antes_de_carregar(self):
        self._instalacao_controlada()
        self.assertTrue(self.modelo.disponivel())
        caminho = self.modelo.model_dir / "pt" / "constituency/cintil_charlm.pt"
        caminho.write_bytes(b"arquivo adulterado")
        with self.assertRaisesRegex(ModeloConstituintesIndisponivel, "Checksum inválido"):
            self.modelo.parse(["Maria", "chegou", "."])
        self.assertFalse(self.modelo.disponivel())

    def test_proveniencia_tem_hashes_reais_e_nao_expoe_cache_mutavel(self):
        self._instalacao_controlada()
        original = self.modelo.provenance()
        self.assertTrue(original["disponivel"])
        nome = "pt/constituency/cintil_charlm.pt"
        esperado = hashlib.sha256((self.modelo.model_dir / nome).read_bytes()).hexdigest()
        self.assertEqual(original["artefatos"][nome]["sha256"], esperado)
        original["artefatos"][nome]["sha256"] = "adulterado"
        original["artefatos"].clear()
        self.assertEqual(self.modelo.provenance()["artefatos"][nome]["sha256"], esperado)

    def test_download_checksum_invalido_preserva_arquivo_preexistente(self):
        destino = self.pasta / "arquivo.pt"
        destino.write_bytes(b"anterior")
        esperado = hashlib.sha256(b"correto").hexdigest()
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"adulterado")):
            with self.assertRaisesRegex(ModeloConstituintesIndisponivel, "Checksum inválido"):
                modulo._baixar_verificado("https://example.org/modelo", destino, "sha256", esperado)
        self.assertEqual(destino.read_bytes(), b"anterior")
        self.assertEqual(list(self.pasta.iterdir()), [destino])

    def test_download_acrescenta_certifi_sem_substituir_confianca_padrao(self):
        destino = self.pasta / "arquivo.pt"
        dados = b"arquivo com checksum valido"
        esperado = hashlib.sha256(dados).hexdigest()
        contexto = Mock(spec=ssl.SSLContext)
        contexto.verify_mode = ssl.CERT_REQUIRED
        contexto.check_hostname = True
        certifi = SimpleNamespace(where=Mock(return_value="/certifi/cacert.pem"))
        caminho_local = "/rede/autoridades-confiaveis.pem"

        def criar_contexto():
            # Sem argumento cafile: o SSL padrão consulta as configurações locais.
            self.assertEqual(os.environ["SSL_CERT_FILE"], caminho_local)
            return contexto

        with patch.dict(os.environ, {"SSL_CERT_FILE": caminho_local}), \
                patch.dict("sys.modules", {"certifi": certifi}), \
                patch.object(modulo.ssl, "create_default_context", side_effect=criar_contexto) as criar, \
                patch("urllib.request.urlopen", return_value=io.BytesIO(dados)) as rede:
            modulo._baixar_verificado("https://example.org/modelo", destino, "sha256", esperado)
            self.assertEqual(os.environ["SSL_CERT_FILE"], caminho_local)

        criar.assert_called_once_with()
        contexto.load_verify_locations.assert_called_once_with(cafile="/certifi/cacert.pem")
        self.assertEqual(contexto.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(contexto.check_hostname)
        rede.assert_called_once_with("https://example.org/modelo", timeout=60, context=contexto)
        self.assertEqual(destino.read_bytes(), dados)

    def test_erro_tls_orienta_confianca_sem_publicar_arquivo(self):
        destino = self.pasta / "arquivo.pt"
        destino.write_bytes(b"anterior")
        esperado = hashlib.sha256(b"correto").hexdigest()
        certificado = ssl.SSLCertVerificationError(1, "certificate verify failed")
        for erro in (certificado, urllib.error.URLError(certificado)):
            with self.subTest(erro=type(erro).__name__), \
                    patch("urllib.request.urlopen", side_effect=erro):
                with self.assertRaisesRegex(
                    ModeloConstituintesIndisponivel, "certifi.*SSL_CERT_FILE"
                ):
                    modulo._baixar_verificado("https://example.org/modelo", destino, "sha256", esperado)
            self.assertEqual(destino.read_bytes(), b"anterior")
            self.assertEqual(list(self.pasta.iterdir()), [destino])

    def test_pipeline_cpu_offline_preserva_contracoes_unicode_e_pontuacao(self):
        tokens = ["Ação", "do", "aluno", "(", "e\u0301", ")", "."]
        documento = SimpleNamespace(sentences=[SimpleNamespace(constituency=arvore_controlada(tokens))])
        pipeline = Mock(return_value=documento)
        fabrica = Mock(return_value=pipeline)
        torch = SimpleNamespace(set_num_threads=Mock())
        stanza = SimpleNamespace(Pipeline=fabrica)
        with patch.object(self.modelo, "_verificar_instalacao"), \
                patch.dict("sys.modules", {"stanza": stanza, "torch": torch}):
            primeira = self.modelo.parse(tokens)
            segunda = self.modelo.parse(tokens)
        self.assertEqual(folhas(primeira), tokens)
        self.assertEqual(segunda, primeira)
        self.assertEqual(pipeline.call_args.args, ([tokens],))
        fabrica.assert_called_once()
        opcoes = fabrica.call_args.kwargs
        self.assertIsNone(opcoes["download_method"])
        self.assertIsNone(opcoes["package"])
        self.assertFalse(opcoes["use_gpu"])
        self.assertTrue(opcoes["tokenize_pretokenized"])
        self.assertEqual(opcoes["processors"], {
            "tokenize": "bosque", "pos": "bosque_nocharlm", "constituency": "cintil_charlm",
        })
        torch.set_num_threads.assert_called_once_with(2)

    def test_folhas_divergentes_ou_reordenadas_sao_rejeitadas(self):
        tokens = ["O", "livro", "do", "aluno", "."]
        for alterados in (tokens[:-1], list(reversed(tokens)), ["O", "livro", "de", "o", "aluno", "."]):
            with self.subTest(folhas=alterados):
                self.modelo._pipeline = Mock(return_value=SimpleNamespace(
                    sentences=[SimpleNamespace(constituency=arvore_controlada(alterados))]
                ))
                with patch.object(self.modelo, "_carregar"):
                    with self.assertRaises(AlinhamentoConstituintesError):
                        self.modelo.parse(tokens)

    def test_falha_de_inferencia_tem_diagnostico_local(self):
        self.modelo._pipeline = Mock(side_effect=RuntimeError("falha de execução controlada"))
        with patch.object(self.modelo, "_carregar"):
            with self.assertRaisesRegex(ModeloConstituintesIndisponivel, "não conseguiu analisar"):
                self.modelo.parse(["Maria", "chegou", "."])

    def test_entradas_invalidas_nao_carregam_modelo(self):
        with patch.object(self.modelo, "_carregar") as carregar:
            for tokens in (None, "Maria chegou.", [], [""], [" "], [5]):
                with self.subTest(tokens=tokens), self.assertRaises(ValueError):
                    self.modelo.parse(tokens)
        carregar.assert_not_called()


@unittest.skipUnless(
    os.environ.get("LINGUA_TESTE_CONSTITUINTES_REAL") == "1",
    "Pesos Stanza reais opcionais: use LINGUA_TESTE_CONSTITUINTES_REAL=1 após --baixar.",
)
class ConstituintesModeloRealTests(unittest.TestCase):
    """Smoke linguístico com limites explícitos, sem converter previsões em gabarito."""

    @classmethod
    def setUpClass(cls):
        cls.modelo = ModeloConstituintes()
        if not cls.modelo.disponivel():
            raise AssertionError(cls.modelo.provenance()["motivo"])

    def test_oracoes_coordenacao_relativa_e_copula(self):
        exemplos = {
            "subordinada": ["Maria", "disse", "que", "João", "chegou", "."],
            "coordenadas": ["Maria", "chegou", "e", "João", "saiu", "."],
            "relativa": ["O", "aluno", "que", "chegou", "cedo", "leu", "o", "livro", "."],
            "copula": ["A", "menina", "é", "inteligente", "."],
        }
        for caso, tokens in exemplos.items():
            with self.subTest(caso=caso):
                arvore = self.modelo.parse(tokens)
                self.assertEqual(folhas(arvore), tokens)
                self.assertEqual(arvore["rotulo"], "ROOT")
                constituintes = []

                def percorrer(no):
                    if "filhos" in no:
                        constituintes.append(no)
                        for filho in no["filhos"]:
                            percorrer(filho)

                percorrer(arvore)
                # CINTIL contém sintagmas NP; não basta uma árvore plana/root
                # com etiquetas lexicais ou uma folha única por palavra.
                self.assertTrue(any(no["rotulo"] == "NP" for no in constituintes))
                self.assertTrue(any(
                    1 < len(folhas(no)) < len(tokens) and any("filhos" in f for f in no["filhos"])
                    for no in constituintes
                ), "O modelo não produziu constituintes internos de várias palavras.")
                if caso in {"relativa", "copula"}:
                    self.assertTrue(any(
                        no["rotulo"] == "NP" and len(folhas(no)) >= 2 for no in constituintes
                    ), "Esperava-se um sintagma nominal para o determinante e o nome.")


if __name__ == "__main__":
    unittest.main()
