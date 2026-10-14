"""Adaptador local e opcional do Stanza para constituintes em português.

Preparação explícita (nenhum download ocorre durante uma requisição HTTP)::

    python modelo_constituintes.py --baixar --verificar

O CINTIL usa suas próprias categorias, preservadas em ``rotulo``. As folhas
precisam corresponder exatamente aos tokens já registrados pelo Língua; o
adaptador nunca expande contrações ou substitui a tokenização existente.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Any
import urllib.error
import urllib.request


STANZA_VERSION = "1.11.0"
RESOURCES_VERSION = "1.11.0"
PACKAGE = "cintil_charlm"
RESOURCES_URL = (
    "https://raw.githubusercontent.com/stanfordnlp/stanza-resources/"
    "main/resources_1.11.0.json"
)
RESOURCES_SHA256 = "e334b4e200aeaa5c1903223de2f3be066f69d3a50a33c70aa1691407628de1ef"
MODEL_BASE_URL = (
    "https://huggingface.co/stanfordnlp/stanza-pt/resolve/v1.11.0/models/"
)
# Checksums publicados no registro oficial, cuja integridade também é fixada.
MODEL_MD5 = {
    "constituency/cintil_charlm.pt": "56a179f851a4e3248aeb8bd940a99e4c",
    "pos/bosque_nocharlm.pt": "93082695b66c8d8068c2b8fa8907daaf",
    "pretrain/conll17.pt": "d712e2572902749a7614e9ba4afcd91b",
    "forward_charlm/oscar2023.pt": "c90551af00702ffbf3affe8caecf112a",
    "backward_charlm/oscar2023.pt": "af5287e320affb27348520051a1c090a",
}


class ModeloConstituintesIndisponivel(RuntimeError):
    """Dependências, pesos ou registro verificado ainda não estão disponíveis."""


class AlinhamentoConstituintesError(ValueError):
    """A árvore não preservou todos os tokens e sua ordem original."""


def _versao_instalada(pacote: str) -> str | None:
    try:
        return importlib.metadata.version(pacote)
    except importlib.metadata.PackageNotFoundError:
        return None


def _hashes(path: Path) -> dict[str, str]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha256 = hashlib.sha256()
    with path.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            md5.update(bloco)
            sha256.update(bloco)
    return {"md5": md5.hexdigest(), "sha256": sha256.hexdigest()}


def _baixar_verificado(url: str, destino: Path, algoritmo: str, esperado: str) -> None:
    """Baixa para arquivo temporário e só publica o artefato após verificá-lo."""
    if destino.is_file() and _hashes(destino)[algoritmo] == esperado:
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destino.parent, delete=False) as arquivo:
            temporario = Path(arquivo.name)
            with urllib.request.urlopen(url, timeout=60) as resposta:
                for bloco in iter(lambda: resposta.read(1024 * 1024), b""):
                    arquivo.write(bloco)
        if _hashes(temporario)[algoritmo] != esperado:
            raise ModeloConstituintesIndisponivel(
                f"Checksum inválido para {destino.name}; o arquivo não foi instalado."
            )
        temporario.replace(destino)
    except (OSError, urllib.error.URLError) as exc:
        raise ModeloConstituintesIndisponivel(
            f"Não foi possível baixar {url}: {exc}. "
            "Confira o acesso de rede e execute --baixar novamente."
        ) from exc
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)


class ModeloConstituintes:
    """Carrega o parser uma única vez, na CPU, e analisa períodos já tokenizados."""

    def __init__(self, model_dir: str | Path | None = None):
        padrao = Path(__file__).resolve().parent / "instance" / "stanza"
        self.model_dir = Path(
            model_dir if model_dir is not None else os.environ.get("LINGUA_STANZA_DIR", padrao)
        ).expanduser().resolve()
        self._pipeline: Any = None
        self._lock = threading.RLock()
        self._cache_assinatura: tuple | None = None
        self._cache_artefatos: dict[str, dict[str, str | int]] | None = None

    def _artefatos(self) -> dict[str, dict[str, str | int]]:
        caminhos = [("resources.json", self.model_dir / "resources.json")]
        caminhos.extend((f"pt/{nome}", self.model_dir / "pt" / nome) for nome in MODEL_MD5)
        try:
            assinatura = tuple(
                (nome, path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_ino)
                for nome, path in caminhos
            )
        except OSError as exc:
            raise ModeloConstituintesIndisponivel(
                "Os pesos de constituintes ainda não foram preparados. "
                "Execute python modelo_constituintes.py --baixar --verificar."
            ) from exc
        if assinatura == self._cache_assinatura and self._cache_artefatos is not None:
            return self._cache_artefatos
        artefatos: dict[str, dict[str, str | int]] = {}
        for nome, path in caminhos:
            hashes = _hashes(path)
            algoritmo = "sha256" if nome == "resources.json" else "md5"
            esperado = RESOURCES_SHA256 if nome == "resources.json" else MODEL_MD5[nome[3:]]
            if hashes[algoritmo] != esperado:
                raise ModeloConstituintesIndisponivel(
                    f"Checksum inválido para {nome}. "
                    "Execute python modelo_constituintes.py --baixar --verificar."
                )
            artefatos[nome] = {**hashes, "bytes": path.stat().st_size}
        self._cache_assinatura = assinatura
        self._cache_artefatos = artefatos
        return artefatos

    def _verificar_instalacao(self) -> None:
        versao = _versao_instalada("stanza")
        if versao != STANZA_VERSION:
            raise ModeloConstituintesIndisponivel(
                f"Stanza {STANZA_VERSION} é necessário (instalado: {versao or 'ausente'}). "
                "Instale requirements-constituintes.txt."
            )
        self._artefatos()

    def disponivel(self) -> bool:
        """Indica se pacote e artefatos íntegros estão preparados, sem carregar pesos."""
        try:
            self._verificar_instalacao()
            return True
        except (ModeloConstituintesIndisponivel, OSError):
            return False

    def provenance(self) -> dict[str, Any]:
        """Identifica o modelo e os arquivos locais, inclusive no estado indisponível."""
        dados: dict[str, Any] = {
            "biblioteca": "stanza",
            "versao": STANZA_VERSION,
            "versao_instalada": _versao_instalada("stanza"),
            "resources_version": RESOURCES_VERSION,
            "pacote": PACKAGE,
            "treebank": "CINTIL",
            "idioma": "pt",
            "pos": "bosque_nocharlm",
            "dispositivo": "cpu",
            "tokenizacao": "tokens_originais_sem_expansao_mwt",
            "resources_sha256": RESOURCES_SHA256,
        }
        try:
            self._verificar_instalacao()
            dados.update(disponivel=True, artefatos={
                nome: dict(metadados) for nome, metadados in self._artefatos().items()
            })
        except (ModeloConstituintesIndisponivel, OSError) as exc:
            dados.update(disponivel=False, motivo=str(exc), artefatos={})
        return dados

    def _carregar(self) -> None:
        self._verificar_instalacao()
        if self._pipeline is not None:
            return
        try:
            import stanza
            import torch

            torch.set_num_threads(2)
            self._pipeline = stanza.Pipeline(
                lang="pt",
                dir=str(self.model_dir),
                package=None,
                processors={
                    "tokenize": "bosque",
                    "pos": "bosque_nocharlm",
                    "constituency": PACKAGE,
                },
                tokenize_pretokenized=True,
                constituency_batch_size=1,
                pos_batch_size=128,
                use_gpu=False,
                download_method=None,
                resources_version=RESOURCES_VERSION,
                verbose=False,
            )
        except Exception as exc:
            raise ModeloConstituintesIndisponivel(
                "Não foi possível carregar o Stanza local: "
                f"{exc}. Instale requirements-constituintes.txt e execute "
                "python modelo_constituintes.py --verificar."
            ) from exc

    def parse(self, tokens: list[str]) -> dict[str, Any]:
        if not isinstance(tokens, list) or not tokens or any(
            not isinstance(token, str) or not token.strip() for token in tokens
        ):
            raise ValueError("Informe uma lista não vazia de tokens de um único período.")
        with self._lock:
            self._carregar()
            try:
                documento = self._pipeline([tokens])
            except Exception as exc:
                raise ModeloConstituintesIndisponivel(
                    f"O modelo local não conseguiu analisar este período: {exc}. "
                    "Execute python modelo_constituintes.py --verificar para conferir a instalação."
                ) from exc
            if len(documento.sentences) != 1:
                raise AlinhamentoConstituintesError("O modelo alterou a divisão do período.")
            arvore = documento.sentences[0].constituency
            if arvore is None or list(arvore.leaf_labels()) != tokens:
                raise AlinhamentoConstituintesError(
                    "As folhas do modelo não correspondem exatamente aos tokens originais."
                )

            def serializar(no: Any) -> dict[str, Any]:
                if no.is_leaf():
                    return {"texto": no.label}
                return {"rotulo": no.label, "filhos": [serializar(filho) for filho in no.children]}

            return serializar(arvore)

    def baixar(self) -> dict[str, Any]:
        """Prepara apenas os cinco artefatos necessários, com verificações oficiais."""
        with self._lock:
            _baixar_verificado(
                RESOURCES_URL, self.model_dir / "resources.json", "sha256", RESOURCES_SHA256
            )
            for nome, esperado in MODEL_MD5.items():
                _baixar_verificado(
                    MODEL_BASE_URL + nome, self.model_dir / "pt" / nome, "md5", esperado
                )
            self._artefatos()
            return self.provenance()


# Nome alternativo para consumidores que tratam o componente como um adaptador.
AdaptadorConstituintes = ModeloConstituintes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baixar", action="store_true", help="Baixa os pesos fixados e verifica checksums.")
    parser.add_argument("--verificar", action="store_true", help="Executa análises reais em português na CPU.")
    parser.add_argument("--diretorio", type=Path, help="Sobrescreve LINGUA_STANZA_DIR e instance/stanza.")
    argumentos = parser.parse_args()
    modelo = ModeloConstituintes(argumentos.diretorio)
    try:
        if argumentos.baixar:
            modelo.baixar()
        if argumentos.verificar:
            # Contrações e subordinação verificam o alinhamento sem alterar tokens.
            exemplos = [
                ["Os", "alunos", "estudam", "na", "biblioteca", "."],
                ["A", "professora", "disse", "que", "o", "aluno", "chegou", "."],
                ["O", "livro", "do", "aluno", "caiu", ",", "mas", "ele", "o", "pegou", "."],
            ]
            resultados = [{"tokens": tokens, "arvore": modelo.parse(tokens)} for tokens in exemplos]
            print(json.dumps({"verificado": True, "exemplos": resultados, "modelo": modelo.provenance()}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(modelo.provenance(), ensure_ascii=False, indent=2))
        return 0 if modelo.disponivel() else 1
    except (ModeloConstituintesIndisponivel, AlinhamentoConstituintesError, OSError) as exc:
        print(json.dumps({"verificado": False, "erro": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
