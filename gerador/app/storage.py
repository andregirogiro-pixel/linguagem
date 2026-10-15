"""Histórico SQLite de planos e gerações, sem persistir credenciais.

Cada chamada a ``gerar_proximo`` reserva somente um item e executa no máximo
duas tentativas de conteúdo, quando a validação ou a diversidade rejeitarem a
primeira. Erros da API não repetem tentativas de conteúdo. Uma interrupção conserva o
item como ``processando``: ele precisa de investigação, nunca de repetição
automática que possa cobrar a mesma geração novamente.
"""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import math
import os
import random
import re
import sqlite3
from time import monotonic
import unicodedata

from .llm_client import ErroGeracao, gerar_paragrafo
from gerador.app.config import ErroParametros, carregar_configuracao
from gerador.app.diversity import avaliar_diversidade, calcular_metricas
from gerador.app.prompt_builder import construir_prompt
from gerador.app.sampler import resolver_plano
from gerador.app.validator import validar_paragrafo


class ErroHistorico(ValueError):
    """Falha pública de validação ou de estado do histórico."""

    def __init__(self, mensagem: str, status_code: int = 400):
        super().__init__(mensagem)
        self.status_code = status_code


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(valor) -> str:
    return json.dumps(valor, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _identificador(valor, nome, limite):
    if (
        not isinstance(valor, str)
        or not valor.strip()
        or len(valor) > limite
        or any(unicodedata.category(c).startswith("C") for c in valor)
        or any(not "!" <= c <= "~" for c in valor.strip())
    ):
        raise ErroHistorico(
            f"Informe {nome} com até {limite} caracteres visíveis, sem espaços internos."
        )
    return valor.strip()


def _inteiro(valor, nome, minimo, maximo=None):
    if (
        isinstance(valor, bool)
        or not isinstance(valor, int)
        or valor < minimo
        or (maximo is not None and valor > maximo)
    ):
        faixa = f"entre {minimo} e {maximo}" if maximo is not None else f"maior ou igual a {minimo}"
        raise ErroHistorico(f"O campo {nome} deve ser um inteiro {faixa}.")
    return valor


def _sem_segredos(valor, chave):
    """Redija a chave usada mesmo se um cliente simulado a devolver por engano."""
    if isinstance(valor, dict):
        return {
            _sem_segredos(str(campo), chave): _sem_segredos(conteudo, chave)
            for campo, conteudo in valor.items()
            if not any(palavra in str(campo).lower() for palavra in ("chave", "secret", "authorization", "api_key", "access_token", "refresh_token"))
            and str(campo).lower() not in {"token", "apikey"}
        }
    if isinstance(valor, list):
        return [_sem_segredos(conteudo, chave) for conteudo in valor]
    if isinstance(valor, str):
        valor = valor.replace(chave, "[credencial removida]") if chave else valor
        return re.sub(r"\bsk-or-v1-[A-Za-z0-9_-]+", "[credencial removida]", valor)
    if isinstance(valor, float) and not math.isfinite(valor):
        return None
    if valor is None or isinstance(valor, (bool, int, float)):
        return valor
    return None


class HistoricoGeracao:
    """Use o mesmo arquivo SQLite da aplicação, com tabelas ``geracao_``.

    Compara o texto à janela de aceitos configurada e aos aceitos do lote;
    resultados fora dessa janela não participam da checagem de repetição.
    Não segura transação enquanto aguarda a API. A reserva ``BEGIN IMMEDIATE``
    torna pedidos simultâneos do mesmo lote mutuamente exclusivos, inclusive
    entre instâncias ou processos distintos que compartilhem o arquivo.
    """

    def __init__(self, database):
        self.database = os.fspath(database)
        with closing(self._conectar()) as conexao, conexao:
            conexao.executescript(
                """
                CREATE TABLE IF NOT EXISTS geracao_lotes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    modelo TEXT NOT NULL,
                    quantidade INTEGER NOT NULL CHECK (quantidade BETWEEN 1 AND 20),
                    parametros_solicitados_json TEXT NOT NULL,
                    controles_json TEXT NOT NULL DEFAULT '{}',
                    metricas_json TEXT NOT NULL DEFAULT '{}',
                    temperatura REAL NOT NULL,
                    criado_em TEXT NOT NULL,
                    atualizado_em TEXT NOT NULL,
                    finalizado_em TEXT,
                    status TEXT NOT NULL CHECK (status IN
                        ('planejado','processando','concluido','parcial','falhou'))
                );
                CREATE TABLE IF NOT EXISTS geracao_itens (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lote_id INTEGER NOT NULL REFERENCES geracao_lotes(id) ON DELETE CASCADE,
                    indice INTEGER NOT NULL,
                    parametros_json TEXT NOT NULL,
                    temperatura REAL NOT NULL,
                    status TEXT NOT NULL CHECK (status IN
                        ('pendente','processando','aceito','rejeitado','erro')),
                    texto TEXT,
                    prompt TEXT,
                    metadados_json TEXT NOT NULL DEFAULT '{}',
                    avisos_json TEXT NOT NULL DEFAULT '[]',
                    tentativas_json TEXT NOT NULL DEFAULT '[]',
                    avaliacao TEXT CHECK (avaliacao IN ('aprovado','reprovado') OR avaliacao IS NULL),
                    erro TEXT,
                    criado_em TEXT NOT NULL,
                    iniciado_em TEXT,
                    finalizado_em TEXT,
                    UNIQUE (lote_id, indice)
                );
                CREATE INDEX IF NOT EXISTS geracao_itens_lote_status
                    ON geracao_itens(lote_id, status, indice);
                CREATE INDEX IF NOT EXISTS geracao_itens_recentes
                    ON geracao_itens(status, finalizado_em DESC, id DESC);
                """
            )
            # Compatibilidade com bancos inicializados enquanto a interface
            # anterior ainda não oferecia temperatura ajustável.
            conexao.execute("BEGIN IMMEDIATE")
            for tabela in ("geracao_lotes", "geracao_itens"):
                colunas = {coluna["name"] for coluna in conexao.execute(f"PRAGMA table_info({tabela})")}
                if "temperatura" not in colunas:
                    padrao = carregar_configuracao()["temperatura"]["padrao"]
                    conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN temperatura REAL")
                    conexao.execute(f"UPDATE {tabela} SET temperatura = ?", (padrao,))
                novas_colunas = (
                    {"controles_json": "TEXT NOT NULL DEFAULT '{}'", "metricas_json": "TEXT NOT NULL DEFAULT '{}'"}
                    if tabela == "geracao_lotes" else
                    {"avisos_json": "TEXT NOT NULL DEFAULT '[]'", "tentativas_json": "TEXT NOT NULL DEFAULT '[]'", "avaliacao": "TEXT"}
                )
                for nome, definicao in novas_colunas.items():
                    if nome not in colunas:
                        conexao.execute(f"ALTER TABLE {tabela} ADD COLUMN {nome} {definicao}")

    def _conectar(self):
        conexao = sqlite3.connect(self.database, timeout=10)
        conexao.row_factory = sqlite3.Row
        conexao.execute("PRAGMA foreign_keys = ON")
        return conexao

    @staticmethod
    def _ler_lote(conexao, lote_id):
        lote = conexao.execute("SELECT * FROM geracao_lotes WHERE id = ?", (lote_id,)).fetchone()
        if lote is None:
            raise ErroHistorico("O lote não foi encontrado.", 404)
        controles = {
            "tema": "", "anti_repeticao": True, "n_historico": 50, "seed": None,
            "modo_sorteio": "ponderado", "temperatura_suportada": True, "max_tentativas": 2,
            **json.loads(lote["controles_json"]),
        }
        itens = []
        for item in conexao.execute(
            "SELECT * FROM geracao_itens WHERE lote_id = ? ORDER BY indice", (lote_id,)
        ):
            itens.append({
                "id": item["id"],
                "lote_id": lote_id,
                "modelo": lote["modelo"],
                "indice": item["indice"],
                "parametros": json.loads(item["parametros_json"]),
                "parametros_finais": json.loads(item["parametros_json"]),
                "temperatura": item["temperatura"] if controles["temperatura_suportada"] else None,
                "controles": controles,
                "avisos": json.loads(item["avisos_json"]),
                "tentativas": json.loads(item["tentativas_json"]),
                "avaliacao": item["avaliacao"],
                "status": item["status"],
                "texto": item["texto"],
                "prompt": item["prompt"],
                "metadados": json.loads(item["metadados_json"]),
                "erro": item["erro"],
                "criado_em": item["criado_em"],
                "iniciado_em": item["iniciado_em"],
                "finalizado_em": item["finalizado_em"],
            })
        contagens = {estado: sum(i["status"] == estado for i in itens) for estado in ("aceito", "rejeitado", "erro")}
        return {
            "id": lote["id"],
            "modelo": lote["modelo"],
            "quantidade": lote["quantidade"],
            "parametros_solicitados": json.loads(lote["parametros_solicitados_json"]),
            "temperatura": lote["temperatura"] if controles["temperatura_suportada"] else None,
            "controles": controles,
            "metricas": json.loads(lote["metricas_json"]),
            "criado_em": lote["criado_em"],
            "atualizado_em": lote["atualizado_em"],
            "finalizado_em": lote["finalizado_em"],
            "status": lote["status"],
            "itens": itens,
            "concluidos": sum(contagens.values()),
            "aceitos": contagens["aceito"],
            "rejeitados": contagens["rejeitado"],
            "erros": contagens["erro"],
        }

    def criar_lote(
        self, modelo, parametros, quantidade, temperatura=None, *, tema="",
        anti_repeticao=None, n_historico=None, seed=None, modo_sorteio="ponderado",
        temperatura_suportada=True, max_tentativas=None,
    ):
        """Valide e grave o plano concreto completo sem chamar qualquer API."""
        modelo = _identificador(modelo, "o modelo", 200)
        quantidade = _inteiro(quantidade, "quantidade", 1, 20)
        try:
            config = carregar_configuracao()
        except ErroParametros as erro:
            raise ErroHistorico(str(erro)) from None
        anti_repeticao = config.get("anti_repeticao", True) if anti_repeticao is None else anti_repeticao
        n_historico = config.get("n_historico", 50) if n_historico is None else n_historico
        max_tentativas = config.get("max_tentativas", 2) if max_tentativas is None else max_tentativas
        if not isinstance(anti_repeticao, bool) or not isinstance(temperatura_suportada, bool):
            raise ErroHistorico("Anti-repetição e suporte à temperatura devem ser booleanos.")
        _inteiro(n_historico, "n_historico", 1, 200)
        _inteiro(max_tentativas, "max_tentativas", 1, 2)
        if seed is not None:
            if isinstance(seed, bool) or not isinstance(seed, int) or not -(2**63) <= seed < 2**63:
                raise ErroHistorico("A seed deve ser um número inteiro de 64 bits.")
        if modo_sorteio not in ("ponderado", "uniforme"):
            raise ErroHistorico("O modo de sorteio deve ser ponderado ou uniforme.")
        if (
            not isinstance(tema, str) or len(tema) > 2000
            or any(unicodedata.category(c).startswith("C") and c not in "\n\t" for c in tema)
        ):
            raise ErroHistorico("O tema deve ser um texto com até 2000 caracteres.")
        controles = {
            "tema": tema.strip(), "anti_repeticao": anti_repeticao,
            "n_historico": n_historico, "seed": seed, "modo_sorteio": modo_sorteio,
            "temperatura_suportada": temperatura_suportada, "max_tentativas": max_tentativas,
        }
        parametros_plano = dict(parametros) if isinstance(parametros, dict) else parametros
        if (
            quantidade > 1 and not temperatura_suportada and isinstance(parametros_plano, dict)
            and parametros_plano.get("ousadia", "aleatorio") == "aleatorio"
        ):
            parametros_plano["ousadia"] = "variar"
        try:
            resolvido = resolver_plano(parametros_plano, quantidade, seed=seed, modo_sorteio=modo_sorteio)
        except ErroParametros as erro:
            raise ErroHistorico(str(erro)) from None
        plano = resolvido["planos"]
        avisos = resolvido["avisos_por_item"]
        configuracao = config["temperatura"]
        temperatura = configuracao["padrao"] if temperatura is None else temperatura
        if (
            isinstance(temperatura, bool)
            or not isinstance(temperatura, (int, float))
            or not math.isfinite(temperatura)
            or not configuracao["min"] <= temperatura <= configuracao["max"]
        ):
            raise ErroHistorico(
                f"A temperatura deve ser um número entre {configuracao['min']} e {configuracao['max']}."
            )
        temperatura = float(temperatura)
        temperaturas = [temperatura] * quantidade
        if quantidade > 1 and temperatura_suportada:
            jitter = configuracao["jitter_lote"]
            rng = random.SystemRandom() if seed is None else random.Random(seed)
            temperaturas = [
                round(min(configuracao["max"], max(configuracao["min"], temperatura + rng.uniform(-jitter, jitter))), 6)
                for _ in plano
            ]
        # JSON sem NaN mantém exportações interoperáveis e registra a solicitação
        # exata, incluindo escolhas aleatório/variar, separada do plano resolvido.
        parametros_json = _json(parametros)
        agora = _agora()
        with closing(self._conectar()) as conexao, conexao:
            cursor = conexao.execute(
                "INSERT INTO geracao_lotes (modelo, quantidade, parametros_solicitados_json, temperatura, controles_json, "
                "criado_em, atualizado_em, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'planejado')",
                (modelo, quantidade, parametros_json, temperatura, _json(controles), agora, agora),
            )
            lote_id = cursor.lastrowid
            conexao.executemany(
                "INSERT INTO geracao_itens (lote_id, indice, parametros_json, temperatura, avisos_json, status, criado_em) "
                "VALUES (?, ?, ?, ?, ?, 'pendente', ?)",
                [(lote_id, indice, _json(item), temperaturas[indice - 1], _json(avisos[indice - 1]), agora) for indice, item in enumerate(plano, start=1)],
            )
            return self._ler_lote(conexao, lote_id)

    def obter_lote(self, lote_id):
        _inteiro(lote_id, "lote", 1)
        with closing(self._conectar()) as conexao:
            return self._ler_lote(conexao, lote_id)

    @staticmethod
    def _textos_anteriores(conexao, lote_id=None, limite=50):
        if limite == 0:
            return []
        recentes = list(conexao.execute(
            "SELECT id, texto FROM geracao_itens WHERE status = 'aceito' "
            "ORDER BY finalizado_em DESC, id DESC LIMIT ?", (limite,)
        ))
        if lote_id is not None:
            recentes.extend(conexao.execute(
                "SELECT id, texto FROM geracao_itens WHERE lote_id = ? AND status = 'aceito' ORDER BY indice",
                (lote_id,),
            ))
        por_id = {linha["id"]: linha["texto"] for linha in recentes}
        return list(dict.fromkeys(por_id[item_id] for item_id in sorted(por_id)))

    def textos_recentes(self, limite=50):
        _inteiro(limite, "limite", 1, 200)
        with closing(self._conectar()) as conexao:
            return self._textos_anteriores(conexao, limite=limite)

    def _reservar(self, lote_id):
        with closing(self._conectar()) as conexao, conexao:
            conexao.execute("BEGIN IMMEDIATE")
            lote = self._ler_lote(conexao, lote_id)
            if any(item["status"] == "processando" for item in lote["itens"]):
                raise ErroHistorico(
                    "Este lote já tem uma geração em andamento. Aguarde antes de continuar.", 409
                )
            item = next((item for item in lote["itens"] if item["status"] == "pendente"), None)
            if item is None:
                return None
            agora = _agora()
            conexao.execute(
                "UPDATE geracao_lotes SET status = 'processando', atualizado_em = ? WHERE id = ?",
                (agora, lote_id),
            )
            return self._iniciar_tentativa(conexao, lote, item)

    def _iniciar_tentativa(self, conexao, lote, item):
        controles = lote["controles"]
        anteriores = self._textos_anteriores(conexao, lote_id=lote["id"], limite=controles["n_historico"]) if controles["anti_repeticao"] else []
        numero = len(item["tentativas"]) + 1
        if controles["anti_repeticao"]:
            anteriores.extend(t["texto"] for t in item["tentativas"] if t.get("texto"))
        prompt = construir_prompt(
            item["parametros"], anteriores, tema=controles["tema"],
            anti_repeticao=controles["anti_repeticao"], n_historico=controles["n_historico"],
            template_indice=item["indice"] + numero - 2,
        )
        if item["tentativas"]:
            prompt += "\nA tentativa anterior foi rejeitada. Corrija estes problemas e produza um novo parágrafo: " + item["tentativas"][-1]["erro"]
        agora = _agora()
        tentativa = {
            "numero": numero, "status": "processando", "prompt": prompt,
            "parametros": item["parametros"], "temperatura": item["temperatura"],
            "texto": None, "metadados": {}, "erro": None,
            "criado_em": agora, "finalizado_em": None,
        }
        tentativas = [*item["tentativas"], tentativa]
        conexao.execute(
            "UPDATE geracao_itens SET status = 'processando', prompt = ?, tentativas_json = ?, iniciado_em = COALESCE(iniciado_em, ?) WHERE id = ?",
            (prompt, _json(tentativas), agora, item["id"]),
        )
        return {**item, "modelo": lote["modelo"], "prompt": prompt, "tentativas": tentativas, "numero_tentativa": numero, "controles": controles}

    @staticmethod
    def _atualizar_estado(conexao, lote_id, agora):
        estados = [linha["status"] for linha in conexao.execute(
            "SELECT status FROM geracao_itens WHERE lote_id = ?", (lote_id,)
        )]
        if "pendente" in estados or "processando" in estados:
            estado, fim = "processando", None
        elif all(estado == "aceito" for estado in estados):
            estado, fim = "concluido", agora
        elif "aceito" in estados:
            estado, fim = "parcial", agora
        else:
            estado, fim = "falhou", agora
        metricas = {}
        if fim is not None:
            aceitos = list(conexao.execute("SELECT texto, parametros_json FROM geracao_itens WHERE lote_id = ? AND status = 'aceito' ORDER BY indice", (lote_id,)))
            metricas = calcular_metricas([i["texto"] for i in aceitos], [json.loads(i["parametros_json"]) for i in aceitos])
        conexao.execute(
            "UPDATE geracao_lotes SET status = ?, atualizado_em = ?, finalizado_em = ?, metricas_json = ? WHERE id = ?",
            (estado, agora, fim, _json(metricas), lote_id),
        )

    def gerar_proximo(self, lote_id, chave_api, gerador=None):
        """Reserve um item e registre até duas tentativas de conteúdo completas."""
        _inteiro(lote_id, "lote", 1)
        chave_api = _identificador(chave_api, "a chave de API", 4096)
        try:
            reservado = self._reservar(lote_id)
        except ErroParametros as erro:
            raise ErroHistorico(str(erro)) from None
        if reservado is None:
            return self.obter_lote(lote_id)
        gerador = gerar_paragrafo if gerador is None else gerador
        while True:
            resultado = self._executar_tentativa(reservado, chave_api, gerador)
            lote, repetir = self._registrar_tentativa(lote_id, reservado, resultado)
            if not repetir:
                return lote
            with closing(self._conectar()) as conexao, conexao:
                conexao.execute("BEGIN IMMEDIATE")
                lote_atual = self._ler_lote(conexao, lote_id)
                item = next(i for i in lote_atual["itens"] if i["id"] == reservado["id"])
                reservado = self._iniciar_tentativa(conexao, lote_atual, item)

    @staticmethod
    def _executar_tentativa(reservado, chave_api, gerador):
        inicio = monotonic()
        texto, erro_publico, metadados = None, None, {}
        estado = "erro"
        try:
            resultado = gerador(
                "openrouter", reservado["modelo"], chave_api,
                prompt=reservado["prompt"], retorno_detalhado=True, temperatura=reservado["temperatura"],
            )
            if (
                not isinstance(resultado, dict)
                or not isinstance(resultado.get("texto"), str)
                or not resultado["texto"].strip()
                or not isinstance(resultado.get("metadados", {}), dict)
            ):
                raise ErroGeracao("O provedor devolveu uma resposta inválida.", 502)
            texto = _sem_segredos(resultado["texto"].strip(), chave_api)
            metadados = _sem_segredos(resultado.get("metadados", {}), chave_api)
            validacao = validar_paragrafo(texto, reservado["parametros"])
            metadados["validacao"] = validacao
            estado = "aceito" if validacao["aceito"] else "rejeitado"
            if estado == "rejeitado":
                erro_publico = "O texto foi guardado no histórico, mas não atende aos parâmetros: " + ", ".join(validacao["motivos"]) + "."
            # Valide antes da gravação; respostas indevidas não deixam um item
            # eternamente processando por conter tipos não serializáveis.
            _json(metadados)
        except ErroGeracao as erro:
            erro_publico = _sem_segredos(str(erro), chave_api)
            metadados_brutos = getattr(erro, "metadados", {})
            metadados = _sem_segredos(metadados_brutos, chave_api) if isinstance(metadados_brutos, dict) else {}
            metadados["erro_status_code"] = erro.status_code
            if getattr(erro, "tipo", "api") == "conteudo":
                estado = "rejeitado"
                texto_bruto = getattr(erro, "texto_gerado", None)
                texto = _sem_segredos(texto_bruto, chave_api) if isinstance(texto_bruto, str) else None
                metadados["validacao"] = {"aceito": False, "motivos": [erro_publico], "metricas": {}}
        except Exception:
            # Nunca copie exceções de clientes externos: podem conter headers,
            # URLs privadas ou o corpo da resposta do provedor.
            erro_publico = "Não foi possível concluir esta geração. Nenhuma tentativa adicional de conteúdo foi executada."
            metadados = {"erro_status_code": 502}
            estado = "erro"
        metadados["duracao_ms"] = round((monotonic() - inicio) * 1000)
        return {"texto": texto, "erro": erro_publico, "metadados": metadados, "status": estado}

    def _registrar_tentativa(self, lote_id, reservado, resultado):
        estado, texto = resultado["status"], resultado["texto"]
        erro_publico, metadados = resultado["erro"], resultado["metadados"]
        agora = _agora()
        with closing(self._conectar()) as conexao, conexao:
            conexao.execute("BEGIN IMMEDIATE")
            controles = reservado["controles"]
            if estado == "aceito" and controles["anti_repeticao"]:
                # Outro lote pode ter terminado enquanto esta chamada aguardava
                # a API. Confira os aceitos atuais sob o lock de gravação para
                # não aprovar duas respostas iguais em lotes concorrentes.
                anteriores = self._textos_anteriores(conexao, lote_id=lote_id, limite=controles["n_historico"])
                diversidade = avaliar_diversidade(texto, anteriores, anti_repeticao=True, n_historico=controles["n_historico"])
                metadados["diversidade"] = diversidade
                if not diversidade["aceito"]:
                    estado = "rejeitado"
                    erro_publico = "O texto foi guardado no histórico, mas rejeitado por repetição: " + ", ".join(diversidade["motivos"]) + "."
            repetir = estado == "rejeitado" and reservado["numero_tentativa"] < controles["max_tentativas"]
            tentativas = reservado["tentativas"]
            tentativas[-1] = {**tentativas[-1], "status": estado, "texto": texto, "erro": erro_publico, "metadados": metadados, "finalizado_em": agora}
            avisos = list(reservado["avisos"])
            avisos_recebidos = metadados.get("avisos", [])
            if isinstance(avisos_recebidos, list):
                avisos.extend(aviso for aviso in avisos_recebidos if isinstance(aviso, str))
            avisos = list(dict.fromkeys(avisos))
            metadados["tentativas_conteudo"] = len(tentativas)
            conexao.execute(
                "UPDATE geracao_itens SET status = ?, texto = ?, metadados_json = ?, erro = ?, finalizado_em = ?, tentativas_json = ?, avisos_json = ? "
                "WHERE id = ? AND status = 'processando'",
                ("processando" if repetir else estado, texto, _json(metadados), erro_publico, None if repetir else agora, _json(tentativas), _json(avisos), reservado["id"]),
            )
            self._atualizar_estado(conexao, lote_id, agora)
            return self._ler_lote(conexao, lote_id), repetir

    def listar_lotes(self, limit=20, offset=0):
        _inteiro(limit, "limit", 1, 100)
        _inteiro(offset, "offset", 0)
        with closing(self._conectar()) as conexao:
            ids = [linha["id"] for linha in conexao.execute(
                "SELECT id FROM geracao_lotes ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
            )]
            return [self._ler_lote(conexao, lote_id) for lote_id in ids]

    def listar_geracoes(self, limite=20, lote_id=None, offset=0):
        """Liste itens com filtro por lote, preservando tentativas e avaliações."""
        _inteiro(limite, "limite", 1, 100)
        _inteiro(offset, "offset", 0)
        if lote_id is not None:
            _inteiro(lote_id, "lote", 1)
        with closing(self._conectar()) as conexao:
            condicao = " WHERE lote_id = ?" if lote_id is not None else ""
            argumentos = ([lote_id] if lote_id is not None else []) + [limite, offset]
            linhas = list(conexao.execute(
                "SELECT id, lote_id FROM geracao_itens" + condicao + " ORDER BY id DESC LIMIT ? OFFSET ?", argumentos
            ))
            lotes = {linha["lote_id"]: self._ler_lote(conexao, linha["lote_id"]) for linha in linhas}
            itens = {item["id"]: item for lote in lotes.values() for item in lote["itens"]}
            return [itens[linha["id"]] for linha in linhas]

    def obter_item(self, item_id):
        _inteiro(item_id, "item", 1)
        with closing(self._conectar()) as conexao:
            item = conexao.execute("SELECT lote_id FROM geracao_itens WHERE id = ?", (item_id,)).fetchone()
            if item is None:
                raise ErroHistorico("A geração não foi encontrada.", 404)
            lote = self._ler_lote(conexao, item["lote_id"])
            return next(i for i in lote["itens"] if i["id"] == item_id)

    def avaliar_item(self, item_id, aprovado):
        """A avaliação humana não altera o resultado da validação automática."""
        _inteiro(item_id, "item", 1)
        if aprovado is not None and not isinstance(aprovado, bool):
            raise ErroHistorico("A avaliação deve ser verdadeiro, falso ou nulo.")
        avaliacao = None if aprovado is None else ("aprovado" if aprovado else "reprovado")
        with closing(self._conectar()) as conexao, conexao:
            conexao.execute("BEGIN IMMEDIATE")
            item = conexao.execute("SELECT lote_id FROM geracao_itens WHERE id = ?", (item_id,)).fetchone()
            if item is None:
                raise ErroHistorico("A geração não foi encontrada.", 404)
            conexao.execute("UPDATE geracao_itens SET avaliacao = ? WHERE id = ?", (avaliacao, item_id))
            lote = self._ler_lote(conexao, item["lote_id"])
            return next(i for i in lote["itens"] if i["id"] == item_id)

    def exportar_lote(self, lote_id):
        """Inclua plano, prompt, textos aceitos/rejeitados e dados de execução."""
        return self.obter_lote(lote_id)
