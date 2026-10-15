"""Credencial local de geração compartilhada pelos modelos do OpenRouter."""

import json
import os
import stat
import tempfile
import threading


PROVEDORES = ("openrouter",)


class ErroConfiguracaoGeracao(Exception):
    """Falha com mensagem pública que não expõe credenciais nem dados do arquivo."""

    def __init__(self, mensagem, status_code=400):
        super().__init__(mensagem)
        self.status_code = status_code


def _validar_provedor(provedor):
    if not isinstance(provedor, str) or provedor not in PROVEDORES:
        raise ErroConfiguracaoGeracao("Selecione OpenRouter.")


def _chave_valida(chave_api):
    return (
        isinstance(chave_api, str)
        and len(chave_api) <= 4096
        and all("!" <= caractere <= "~" for caractere in chave_api)
    )


class ConfiguracaoGeracao:
    """Guarda chaves em JSON local privado, sem devolvê-las no status público.

    O caminho deve ficar fora de arquivos versionados. Instâncias que usam o
    mesmo caminho compartilham um lock para preservar atualizações em threads.
    """

    _locks = {}
    _locks_guard = threading.Lock()

    def __init__(self, path):
        self.path = os.path.abspath(os.fspath(path))
        with self._locks_guard:
            self._lock = self._locks.setdefault(self.path, threading.RLock())

    def status(self):
        """Informe apenas se existe uma credencial do OpenRouter."""
        with self._lock:
            dados = self._ler()
            return {provedor: bool(dados.get(provedor, "")) for provedor in PROVEDORES}

    def obter(self, provedor):
        """Retorne a credencial somente para o transporte interno da API."""
        _validar_provedor(provedor)
        with self._lock:
            return self._ler().get(provedor, "")

    def salvar(self, provedor, chave_api):
        """Salve a chave do provedor; uma string vazia remove sua credencial."""
        _validar_provedor(provedor)
        if not _chave_valida(chave_api):
            raise ErroConfiguracaoGeracao(
                "A chave deve ter até 4096 caracteres ASCII visíveis, sem espaços."
            )
        with self._lock:
            dados = self._ler()
            if chave_api:
                dados[provedor] = chave_api
            else:
                dados.pop(provedor, None)
            self._gravar(dados)

    def _ler(self):
        fd = None
        try:
            # O_NOFOLLOW impede a leitura de um destino externo por link simbólico.
            # O_NONBLOCK também evita bloquear caso o destino seja um arquivo especial.
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            if os.path.islink(self.path):
                raise OSError("Arquivo de configuração inválido")
            fd = os.open(self.path, flags)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("Arquivo de configuração inválido")
            with os.fdopen(fd, "r", encoding="utf-8") as arquivo:
                fd = None
                conteudo = arquivo.read(100001)
            if len(conteudo) > 100000:
                raise ValueError("Arquivo de configuração inválido")
            dados = json.loads(conteudo)
            if not isinstance(dados, dict) or any(
                provedor not in PROVEDORES or not _chave_valida(chave)
                for provedor, chave in dados.items()
            ):
                raise ValueError("Arquivo de configuração inválido")
            return dados
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise ErroConfiguracaoGeracao(
                "Não foi possível ler as chaves salvas. Verifique o arquivo de configuração local.",
                status_code=500,
            ) from None
        finally:
            if fd is not None:
                os.close(fd)

    def _gravar(self, dados):
        temporario = None
        fd = None
        try:
            diretorio = os.path.dirname(self.path)
            os.makedirs(diretorio, mode=0o700, exist_ok=True)
            fd, temporario = tempfile.mkstemp(prefix=".geracao_chaves-", suffix=".tmp", dir=diretorio)
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            else:
                os.chmod(temporario, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as arquivo:
                fd = None
                json.dump(dados, arquivo, ensure_ascii=True)
                arquivo.write("\n")
                arquivo.flush()
                os.fsync(arquivo.fileno())
            os.replace(temporario, self.path)
            temporario = None
        except (OSError, ValueError, UnicodeError):
            raise ErroConfiguracaoGeracao(
                "Não foi possível salvar as chaves. Verifique o armazenamento local.", status_code=500
            ) from None
        finally:
            if fd is not None:
                os.close(fd)
            if temporario is not None:
                try:
                    os.unlink(temporario)
                except OSError:
                    pass
