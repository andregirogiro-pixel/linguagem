"""A chave do OpenRouter persiste sem aparecer no status público."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from configuracao_geracao import ConfiguracaoGeracao, ErroConfiguracaoGeracao


class ConfiguracaoGeracaoTests(unittest.TestCase):
    def setUp(self):
        self.temporario = TemporaryDirectory()
        self.addCleanup(self.temporario.cleanup)
        self.caminho = Path(self.temporario.name) / "instance" / "geracao_chaves.json"
        self.configuracao = ConfiguracaoGeracao(self.caminho)

    def test_nenhuma_chave_inicial_persistencia_atualizacao_e_remocao(self):
        self.assertEqual(self.configuracao.status(), {"openrouter": False})
        self.assertEqual(self.configuracao.obter("openrouter"), "")
        self.assertFalse(self.caminho.exists())

        self.configuracao.salvar("openrouter", "credencial-openrouter")
        reiniciada = ConfiguracaoGeracao(str(self.caminho))
        self.assertEqual(reiniciada.obter("openrouter"), "credencial-openrouter")
        self.assertEqual(reiniciada.status(), {"openrouter": True})
        self.assertNotIn("credencial", json.dumps(reiniciada.status()))

        reiniciada.salvar("openrouter", "credencial-atualizada")
        self.assertEqual(self.configuracao.obter("openrouter"), "credencial-atualizada")
        reiniciada.salvar("openrouter", "")
        self.assertEqual(self.configuracao.status(), {"openrouter": False})
        self.assertEqual(self.configuracao.obter("openrouter"), "")

    def test_arquivo_criado_e_atualizado_com_permissoes_privadas(self):
        self.configuracao.salvar("openrouter", "credencial")
        self.assertEqual(stat.S_IMODE(self.caminho.stat().st_mode), 0o600)
        os.chmod(self.caminho, 0o644)
        self.configuracao.salvar("openrouter", "outra-credencial")
        self.assertEqual(stat.S_IMODE(self.caminho.stat().st_mode), 0o600)
        self.assertEqual(list(self.caminho.parent.glob(".geracao_chaves-*.tmp")), [])

    def test_gravacao_sem_fchmod_usa_chmod_no_temporario(self):
        with patch("configuracao_geracao.os.fchmod", create=True):
            del os.fchmod
            with patch("configuracao_geracao.os.chmod", wraps=os.chmod) as chmod:
                self.configuracao.salvar("openrouter", "credencial")
        chmod.assert_called_once()
        caminho, modo = chmod.call_args.args
        self.assertEqual(Path(caminho).parent, self.caminho.parent)
        self.assertNotEqual(Path(caminho), self.caminho)
        self.assertEqual(modo, 0o600)
        self.assertEqual(stat.S_IMODE(self.caminho.stat().st_mode), 0o600)
        self.assertEqual(self.configuracao.obter("openrouter"), "credencial")

    def test_validacao_nao_expoe_credenciais_nem_cria_arquivo(self):
        for provedor in (None, [], "outro", "openai", "OPENAI"):
            with self.subTest(provedor=provedor):
                with self.assertRaises(ErroConfiguracaoGeracao) as erro:
                    self.configuracao.salvar(provedor, "credencial-privada")
                self.assertEqual(erro.exception.status_code, 400)
                self.assertNotIn("credencial-privada", str(erro.exception))
        for chave in (
            None, 123, [], "x" * 4097, " ", "   ", "chave com espaço",
            "chave-com-acentuação", "chave-😀", "privada\r\ninjetada",
            "privada\0", "privada\u0085", "privada\ud800",
        ):
            with self.subTest(tipo=type(chave).__name__):
                with self.assertRaises(ErroConfiguracaoGeracao) as erro:
                    self.configuracao.salvar("openrouter", chave)
                self.assertEqual(erro.exception.status_code, 400)
                self.assertNotIn("privada", str(erro.exception))
        self.assertFalse(self.caminho.exists())

    def test_aceita_limite_e_caracteres_ascii_visiveis(self):
        chave = "".join(chr(codigo) for codigo in range(33, 127))
        self.configuracao.salvar("openrouter", chave)
        self.assertEqual(self.configuracao.obter("openrouter"), chave)
        self.configuracao.salvar("openrouter", "x" * 4096)
        self.assertEqual(self.configuracao.obter("openrouter"), "x" * 4096)

    def test_arquivo_corrompido_nao_e_sobrescrito(self):
        self.caminho.parent.mkdir()
        for conteudo in (
            '{"openrouter":"segredo-sem-fechar', 'null', '[]', '{"openrouter":123}',
            '{"outro":"segredo"}', '{"openrouter":"segredo com espaços"}',
            "[" * 1500 + "0" + "]" * 1500,
        ):
            with self.subTest(conteudo=conteudo):
                self.caminho.write_text(conteudo, encoding="utf-8")
                for operacao in (
                    self.configuracao.status,
                    lambda: self.configuracao.obter("openrouter"),
                    lambda: self.configuracao.salvar("openrouter", "nova-credencial"),
                ):
                    with self.assertRaises(ErroConfiguracaoGeracao) as erro:
                        operacao()
                    self.assertEqual(erro.exception.status_code, 500)
                    self.assertNotIn("segredo", str(erro.exception))
                    self.assertEqual(self.caminho.read_text(encoding="utf-8"), conteudo)

    def test_link_simbolico_nao_e_lido_ou_substituido(self):
        self.caminho.parent.mkdir()
        destino = self.caminho.parent / "outro.json"
        conteudo = '{"openrouter":"credencial-do-destino"}'
        destino.write_text(conteudo, encoding="utf-8")
        self.caminho.symlink_to(destino)
        for operacao in (self.configuracao.status, lambda: self.configuracao.salvar("openrouter", "nova")):
            with self.assertRaises(ErroConfiguracaoGeracao) as erro:
                operacao()
            self.assertEqual(erro.exception.status_code, 500)
        self.assertTrue(self.caminho.is_symlink())
        self.assertEqual(destino.read_text(encoding="utf-8"), conteudo)

    def test_falha_de_leitura_nao_e_ignorada(self):
        self.configuracao.salvar("openrouter", "credencial-original")
        with patch("configuracao_geracao.os.open", side_effect=PermissionError("segredo-na-excecao")):
            with self.assertRaises(ErroConfiguracaoGeracao) as erro:
                self.configuracao.salvar("openrouter", "credencial-nova")
        self.assertEqual(erro.exception.status_code, 500)
        self.assertNotIn("segredo", str(erro.exception))
        self.assertEqual(self.configuracao.obter("openrouter"), "credencial-original")

    def test_falha_de_troca_atomica_preserva_arquivo_e_remove_temporario(self):
        self.configuracao.salvar("openrouter", "credencial-original")
        with patch("configuracao_geracao.os.replace", side_effect=OSError("segredo-na-excecao")):
            with self.assertRaises(ErroConfiguracaoGeracao) as erro:
                self.configuracao.salvar("openrouter", "credencial-nova")
        self.assertEqual(erro.exception.status_code, 500)
        self.assertNotIn("segredo", str(erro.exception))
        self.assertEqual(self.configuracao.obter("openrouter"), "credencial-original")
        self.assertEqual(list(self.caminho.parent.glob(".geracao_chaves-*.tmp")), [])

    def test_instancias_compartilham_lock_e_salvam_em_threads(self):
        outra = ConfiguracaoGeracao(self.caminho)
        self.assertIs(self.configuracao._lock, outra._lock)
        with ThreadPoolExecutor(max_workers=2) as executor:
            tarefas = [
                executor.submit(configuracao.salvar, "openrouter", f"credencial-{indice}")
                for indice in range(20)
                for configuracao in (self.configuracao, outra)
            ]
            for tarefa in tarefas:
                tarefa.result()
        self.assertEqual(self.configuracao.status(), {"openrouter": True})


if __name__ == "__main__":
    unittest.main()
