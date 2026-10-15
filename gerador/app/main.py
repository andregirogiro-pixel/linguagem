"""API FastAPI do gerador, também usado pelo botão do Lingo."""

import json
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from configuracao_geracao import ConfiguracaoGeracao, ErroConfiguracaoGeracao
from geracao_paragrafo import ErroGeracao
from .config import ErroParametros, carregar_ambiente, catalogo_parametros
from .exporter import exportar_csv, exportar_jsonl
from .models import PedidoAvaliacao, PedidoChave, PedidoLote, PedidoSorteio, PedidoVazio
from .sampler import resolver_plano
from .llm_client import capacidades_modelo
from .storage import ErroHistorico, HistoricoGeracao


RAIZ = Path(__file__).resolve().parents[2]


def _caminho(valor, padrao):
    caminho = Path(valor or padrao).expanduser()
    return caminho if caminho.is_absolute() else RAIZ / caminho


def create_app(database=None, keys_file=None):
    ambiente = carregar_ambiente()
    database = _caminho(database or ambiente.get("GERADOR_DB"), "instance/textos.sqlite3")
    database.parent.mkdir(parents=True, exist_ok=True)
    keys_file = _caminho(keys_file or ambiente.get("GERADOR_KEYS_FILE"), database.parent / "geracao_chaves.json")
    historico = HistoricoGeracao(database)
    chaves = ConfiguracaoGeracao(keys_file)
    app = FastAPI(title="Gerador de parágrafos", version="1.0.0")
    app.state.historico = historico
    app.state.chaves = chaves

    @app.middleware("http")
    async def acesso_local(request, call_next):
        if request.url.path.startswith("/api/") and request.method in {"POST", "PATCH"}:
            origin = request.headers.get("origin")
            if origin is not None and origin != str(request.base_url).rstrip("/"):
                return JSONResponse({"erro": "Use a geração na própria página do aplicativo."}, 403, headers={"Cache-Control": "no-store"})
            length = request.headers.get("content-length", "0")
            if not length.isdecimal() or int(length) > 16 * 1024:
                return JSONResponse({"erro": "A configuração enviada excedeu o limite permitido."}, 413, headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def campos_invalidos(request, error):
        # A resposta padrão do Pydantic pode incluir o valor recebido da chave.
        return JSONResponse({"erro": "Configuração inválida. Confira os campos, os tipos e os limites informados."}, 422)

    async def erro_publico(request, error):
        return JSONResponse({"erro": str(error)}, getattr(error, "status_code", 400))

    for tipo in (ErroParametros, ErroHistorico, ErroConfiguracaoGeracao, ErroGeracao):
        app.add_exception_handler(tipo, erro_publico)

    @app.get("/api/geracao/catalogo")
    def generation_catalog():
        catalogo = catalogo_parametros()
        catalogo["temperatura"]["suportada"] = False
        return catalogo

    @app.post("/api/geracao/sortear")
    def generation_draw(payload: PedidoSorteio):
        plano = resolver_plano({}, 1, seed=payload.seed, modo_sorteio=payload.modo_sorteio)
        return {"parametros": plano["planos"][0], "avisos": plano["avisos_por_item"][0]}

    @app.get("/api/geracao/modelos/capacidades")
    def generation_capabilities(modelo: str):
        return capacidades_modelo(modelo, consultar=True)

    @app.get("/api/geracao/configuracoes")
    def generation_settings():
        return {"chave_salva": chaves.status()["openrouter"]}

    @app.post("/api/geracao/configuracoes")
    def save_settings(payload: PedidoChave):
        chaves.salvar("openrouter", payload.chave_api)
        return generation_settings()

    @app.post("/api/geracao/lotes", status_code=201)
    def generation_create_batch(payload: PedidoLote):
        capacidade = capacidades_modelo(payload.modelo, consultar=False)
        return historico.criar_lote(**payload.model_dump(), temperatura_suportada=capacidade["temperatura"]["suportada"])

    @app.get("/api/geracao/lotes/{batch_id}")
    def generation_batch(batch_id: int):
        return historico.obter_lote(batch_id)

    @app.post("/api/geracao/lotes/{batch_id}/proximo")
    def generation_next(batch_id: int, payload: PedidoVazio):
        chave = chaves.obter("openrouter")
        if not chave:
            raise ErroHistorico("Salve a chave do OpenRouter em Configurações antes de gerar.")
        return historico.gerar_proximo(batch_id, chave)

    @app.get("/api/geracao/historico")
    def generation_history(offset: int = 0, lote_id: int | None = None):
        if offset < 0:
            raise ErroHistorico("Informe uma posição válida para o histórico.")
        if lote_id is not None:
            lotes = [historico.obter_lote(lote_id)] if offset == 0 else []
        else:
            lotes = historico.listar_lotes(limit=20, offset=offset)
        return {"lotes": lotes, "proximo_offset": offset + 20 if len(lotes) == 20 else None}

    @app.patch("/api/geracao/itens/{item_id}/avaliacao")
    def generation_review(item_id: int, payload: PedidoAvaliacao):
        return historico.avaliar_item(item_id, payload.aprovado)

    @app.post("/api/geracao/itens/{item_id}/regenerar", status_code=201)
    def generation_regenerate(item_id: int, payload: PedidoVazio):
        item = historico.obter_item(item_id)
        lote = historico.obter_lote(item["lote_id"])
        controles = dict(lote["controles"])
        controles["temperatura_suportada"] = capacidades_modelo(lote["modelo"], consultar=False)["temperatura"]["suportada"]
        return historico.criar_lote(lote["modelo"], item["parametros"], 1, temperatura=item["temperatura"], **controles)

    @app.get("/api/geracao/lotes/{batch_id}/exportar.{formato}")
    def generation_export(batch_id: int, formato: str):
        lote = historico.exportar_lote(batch_id)
        if formato == "json":
            corpo, tipo = json.dumps(lote, ensure_ascii=False, allow_nan=False), "application/json"
        elif formato == "jsonl":
            corpo, tipo = exportar_jsonl(lote), "application/x-ndjson"
        elif formato == "csv":
            corpo, tipo = exportar_csv(lote), "text/csv"
        else:
            return JSONResponse({"erro": "Escolha JSON, JSONL ou CSV."}, 404)
        return Response(corpo, media_type=tipo, headers={"Content-Disposition": f'attachment; filename="lote-{batch_id}.{formato}"'})

    app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index():
        templates = Environment(loader=FileSystemLoader(RAIZ / "gerador" / "frontend"), autoescape=select_autoescape())
        urls = {
            "generate_paragraph": "/api/gerar-paragrafo", "generation_settings": "/api/geracao/configuracoes",
            "generation_catalog": "/api/geracao/catalogo", "generation_draw": "/api/geracao/sortear",
            "generation_create_batch": "/api/geracao/lotes", "generation_history": "/api/geracao/historico",
            "generation_capabilities": "/api/geracao/modelos/capacidades",
        }
        return templates.get_template("pagina.html").render(url_for=lambda name, **kwargs: urls[name])

    return app


app = create_app()
