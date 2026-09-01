from __future__ import annotations

import json
import logging
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings, get_settings
from .domain import ConnectionInput, ExportInput, FieldMapping, GenerateInput
from .errors import CuratorError
from .logging_config import configure_logging, correlation_id_var
from .security import get_or_create_session_hmac_key
from .service import CuratorService, SessionStore, markdown_export

ROOT = Path(os.environ.get("CURATOR_ROOT", Path(__file__).resolve().parents[2]))
logger = logging.getLogger("ai_curator")


def create_app(settings: Settings | None = None, service: CuratorService | None = None) -> FastAPI:
    settings = settings or get_settings()
    mapping_path = settings.data_dir / "field-mapping.json"
    try:
        saved_mapping = FieldMapping.model_validate_json(mapping_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved_mapping = FieldMapping()
    store = SessionStore(
        hmac_key=get_or_create_session_hmac_key(settings.hmac_key_file),
        default_mapping=saved_mapping,
    )
    curator = service or CuratorService(settings, ROOT)
    csrf_tokens: dict[str, str] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        configure_logging()
        yield

    app = FastAPI(title="ИИ-Куратор", version="0.1.0", docs_url=None if settings.production else "/docs", redoc_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.middleware("http")
    async def security_middleware(request: Request, call_next):
        started = time.monotonic()
        correlation_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex
        correlation_id_var.set(correlation_id[:64])
        session_id = request.cookies.get("curator_session")
        new_session = not session_id or len(session_id) != 43
        if new_session:
            session_id = secrets.token_urlsafe(32)
        request.state.session_id = session_id
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            host_header = request.headers.get("Host", "")
            allowed_origins = {f"http://{host_header}", f"https://{host_header}"}
            if origin and origin not in allowed_origins:
                return JSONResponse({"error": {"code": "ORIGIN_REJECTED", "message": "Запрос с другого сайта отклонён."}}, status_code=403)
            expected = csrf_tokens.get(session_id)
            supplied = request.headers.get("X-CSRF-Token")
            if (
                request.url.path.startswith("/api/")
                and request.url.path != "/api/session"
                and (not expected or not supplied or not secrets.compare_digest(supplied, expected))
            ):
                return JSONResponse({"error": {"code": "CSRF_REJECTED", "message": "Сессия устарела. Обновите страницу."}}, status_code=403)
        response = await call_next(request)
        if new_session:
            response.set_cookie("curator_session", session_id, httponly=True, samesite="strict", secure=False, max_age=None)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Correlation-ID"] = correlation_id[:64]
        logger.info("request_completed", extra={"duration_ms": round((time.monotonic() - started) * 1000), "code": response.status_code})
        return response

    @app.exception_handler(CuratorError)
    async def curator_exception(_: Request, exc: CuratorError):
        logger.warning("controlled_error", extra={"code": exc.code})
        return JSONResponse({"error": {"code": exc.code, "message": exc.user_message}}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unexpected_exception(_: Request, exc: Exception):
        logger.exception("unexpected_error", extra={"code": type(exc).__name__})
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "Неожиданная ошибка. Повторите действие или откройте диагностику."}}, status_code=500)

    def state(request: Request):
        return store.get(request.state.session_id)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return FileResponse(ROOT / "src/ai_curator/static/index.html")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "version": "0.1.0"}

    @app.get("/api/session")
    async def session(request: Request):
        token = secrets.token_urlsafe(32)
        csrf_tokens[request.state.session_id] = token
        return {"csrf_token": token}

    @app.post("/api/connect")
    async def connect(request: Request, body: ConnectionInput):
        return await curator.connect(state(request), base_url=body.jira_base_url, token=body.token, ca_bundle_path=body.ca_bundle_path, proxy_url=body.proxy_url)

    @app.get("/api/employees")
    async def employees(
        request: Request,
        q: str | None = Query(default=None, max_length=100),
        billable_only: bool = False,
        page: int = Query(default=1, ge=1, le=10_000),
        page_size: int = Query(default=20, ge=1, le=100),
    ):
        all_items = await curator.employees(state(request), q, billable_only)
        start = (page - 1) * page_size
        return {
            "items": all_items[start : start + page_size],
            "page": page,
            "page_size": page_size,
            "total": len(all_items),
            "has_next": start + page_size < len(all_items),
        }

    @app.get("/api/fields")
    async def fields(request: Request):
        return await curator.field_catalog(state(request))

    @app.put("/api/mapping")
    async def mapping(request: Request, body: FieldMapping):
        session_state = state(request)
        session_state.mapping = body
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            mapping_file = settings.data_dir / "field-mapping.json"
            temporary = settings.data_dir / ".field-mapping.tmp"
            temporary.write_text(body.model_dump_json(indent=2), encoding="utf-8")
            temporary.replace(mapping_file)
        except OSError as exc:
            raise CuratorError(
                "MAPPING_SAVE_FAILED",
                "Не удалось сохранить mapping. Проверьте локальный каталог данных.",
                500,
            ) from exc
        return {"ok": True, "warnings": curator.mapping_warnings(body)}

    @app.post("/api/employees/{key}/load")
    async def load_employee(request: Request, key: str, include_display_name: bool = False):
        return await curator.load_employee(state(request), key, include_display_name)

    @app.post("/api/generate")
    async def generate(request: Request, body: GenerateInput):
        return await curator.generate(state(request), body.generation_id, set(body.excluded_source_refs), body.include_display_name)

    @app.post("/api/generate/{generation_id}/cancel")
    async def cancel(request: Request, generation_id: str):
        return {"cancelled": curator.cancel(state(request), generation_id)}

    @app.get("/api/model/diagnostics")
    async def model_diagnostics():
        health = await curator.llm.health()
        return {"runtime": "llama.cpp-compatible", "configured_model": settings.llm_model, "health": health, "contains_personal_data": False}

    @app.post("/api/export")
    async def export(body: ExportInput):
        if body.format == "markdown":
            return PlainTextResponse(markdown_export(body.result), media_type="text/markdown", headers={"Content-Disposition": 'attachment; filename="one-on-one.md"'})
        return Response(json.dumps(body.result, ensure_ascii=False, indent=2), media_type="application/json", headers={"Content-Disposition": 'attachment; filename="one-on-one.json"'})

    @app.delete("/api/session")
    async def delete_session(request: Request):
        store.delete(request.state.session_id)
        csrf_tokens.pop(request.state.session_id, None)
        response = JSONResponse({"ok": True})
        response.delete_cookie("curator_session")
        return response

    app.mount("/static", StaticFiles(directory=ROOT / "src/ai_curator/static"), name="static")
    return app


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("ai_curator.main:app", host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    run()
