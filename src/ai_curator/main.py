from __future__ import annotations

import asyncio
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
from .domain import ConnectionInput, ExportInput, GenerateInput
from .errors import CuratorError
from .export import markdown_export
from .logging_config import configure_logging, correlation_id_var
from .security import get_or_create_session_hmac_key
from .service import CuratorService
from .session import GenerationJob, SessionState, SessionStore

ROOT = Path(os.environ.get("CURATOR_ROOT", Path(__file__).resolve().parents[2]))
logger = logging.getLogger("ai_curator")


def create_app(settings: Settings | None = None, service: CuratorService | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = SessionStore(
        hmac_key=get_or_create_session_hmac_key(settings.hmac_key_file),
    )
    curator = service
    csrf_tokens: dict[str, str] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        nonlocal curator
        configure_logging()
        # Build runtime clients at process startup, not module import. This keeps
        # tooling able to import the app while preserving fail-fast startup when
        # the mandatory privacy filter is not configured.
        curator = curator or CuratorService(settings, ROOT)
        yield

    app = FastAPI(
        title="ИИ-Куратор",
        version="0.1.0",
        docs_url=None if settings.production else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
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
                return JSONResponse(
                    {
                        "error": {
                            "code": "ORIGIN_REJECTED",
                            "message": "Запрос с другого сайта отклонён.",
                        }
                    },
                    status_code=403,
                )
            expected = csrf_tokens.get(session_id)
            supplied = request.headers.get("X-CSRF-Token")
            if (
                request.url.path.startswith("/api/")
                and request.url.path != "/api/session"
                and (not expected or not supplied or not secrets.compare_digest(supplied, expected))
            ):
                return JSONResponse(
                    {
                        "error": {
                            "code": "CSRF_REJECTED",
                            "message": "Сессия устарела. Обновите страницу.",
                        }
                    },
                    status_code=403,
                )
        response = await call_next(request)
        if new_session:
            response.set_cookie(
                "curator_session",
                session_id,
                httponly=True,
                samesite="strict",
                secure=False,
                max_age=None,
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Correlation-ID"] = correlation_id[:64]
        logger.info(
            "request_completed",
            extra={
                "duration_ms": round((time.monotonic() - started) * 1000),
                "code": response.status_code,
            },
        )
        return response

    @app.exception_handler(CuratorError)
    async def curator_exception(_: Request, exc: CuratorError):
        logger.warning("controlled_error", extra={"code": exc.code})
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.user_message}}, status_code=exc.status_code
        )

    @app.exception_handler(Exception)
    async def unexpected_exception(_: Request, exc: Exception):
        logger.exception("unexpected_error", extra={"code": type(exc).__name__})
        return JSONResponse(
            {
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "Неожиданная ошибка. Повторите действие или откройте диагностику.",
                }
            },
            status_code=500,
        )

    def state(request: Request) -> SessionState:
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
        return await curator.connect(
            state(request),
            base_url=body.jira_base_url,
            token=body.token,
            ca_bundle_path=body.ca_bundle_path,
            proxy_url=body.proxy_url,
        )

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

    @app.post("/api/employees/{key}/load")
    async def load_employee(request: Request, key: str, include_display_name: bool = False):
        return await curator.load_employee(state(request), key, include_display_name)

    @app.get("/api/context")
    async def current_context(request: Request):
        session = state(request)
        if session.context is None:
            raise CuratorError(
                "CONTEXT_REQUIRED",
                "Контекст не найден. Выберите сотрудника заново.",
                409,
            )
        return session.context

    def generation_job(session: SessionState, generation_id: str) -> GenerationJob:
        job = session.generation_jobs.get(generation_id)
        if job is None:
            raise CuratorError(
                "GENERATION_NOT_FOUND",
                "Задача генерации не найдена или сессия завершена.",
                404,
            )
        return job

    async def run_generation_job(
        session: SessionState,
        job: GenerationJob,
        excluded_source_refs: set[str],
    ) -> None:
        try:
            result = await curator.generate(
                session,
                job.generation_id,
                excluded_source_refs,
            )
            if job.status == "running":
                job.result = result
                job.status = "succeeded"
        except asyncio.CancelledError:
            job.mark_cancelled()
        except CuratorError as exc:
            if exc.code == "GENERATION_CANCELLED":
                job.mark_cancelled()
            elif job.status == "running":
                job.status = "failed"
                job.error = {"code": exc.code, "message": exc.user_message}
                job.error_status_code = exc.status_code
        except Exception as exc:
            logger.exception(
                "background_generation_failed",
                extra={"code": type(exc).__name__},
            )
            if job.status == "running":
                job.status = "failed"
                job.error = {
                    "code": "INTERNAL_ERROR",
                    "message": "Неожиданная ошибка. Повторите действие или откройте диагностику.",
                }
        finally:
            finished_at = time.monotonic()
            if job.finished_at is None:
                job.finished_at = finished_at
            logger.info(
                "background_generation_completed",
                extra={
                    "code": job.error["code"] if job.error else None,
                    "duration_ms": round((finished_at - job.started_at) * 1000),
                },
            )

    @app.post("/api/generate", status_code=202)
    async def generate(request: Request, body: GenerateInput):
        session = state(request)
        completed_jobs = [
            generation_id
            for generation_id, existing_job in session.generation_jobs.items()
            if existing_job.status != "running"
        ]
        for completed_generation_id in completed_jobs[:-5]:
            session.generation_jobs.pop(completed_generation_id, None)
        generation_id = uuid.uuid4().hex
        job = GenerationJob(generation_id=generation_id)
        session.generation_jobs[generation_id] = job
        job.task = asyncio.create_task(
            run_generation_job(
                session,
                job,
                set(body.excluded_source_refs),
            ),
            name=f"curator-generation-{generation_id}",
        )
        return job.status_payload()

    @app.get("/api/generate/{generation_id}/status")
    async def generation_status(request: Request, generation_id: str):
        return generation_job(state(request), generation_id).status_payload()

    @app.get("/api/generate/{generation_id}/result")
    async def generation_result(request: Request, generation_id: str):
        job = generation_job(state(request), generation_id)
        if job.status == "running":
            return JSONResponse(job.status_payload(), status_code=202)
        if job.status != "succeeded" or job.result is None:
            return JSONResponse(
                {"error": job.error},
                status_code=job.error_status_code if job.status == "failed" else 409,
            )
        return {
            "generation_id": generation_id,
            "status": job.status,
            "result": job.result,
        }

    @app.post("/api/generate/{generation_id}/cancel")
    async def cancel(request: Request, generation_id: str):
        session = state(request)
        job = generation_job(session, generation_id)
        if job.status != "running":
            return {"cancelled": False, **job.status_payload()}
        cancelled = curator.cancel(session, generation_id)
        if not cancelled and job.task is not None and not job.task.done():
            job.task.cancel()
            cancelled = True
        if cancelled:
            job.mark_cancelled()
        return {"cancelled": cancelled, **job.status_payload()}

    @app.get("/api/model/diagnostics")
    async def model_diagnostics():
        return await curator.model_diagnostics()

    @app.get("/api/model/privacy-filter/diagnostics")
    async def privacy_filter_diagnostics():
        return await curator.privacy_filter_diagnostics()

    @app.post("/api/export")
    async def export(body: ExportInput):
        if body.format == "markdown":
            return PlainTextResponse(
                markdown_export(body.result),
                media_type="text/markdown",
                headers={"Content-Disposition": 'attachment; filename="one-on-one.md"'},
            )
        return Response(
            json.dumps(body.result, ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="one-on-one.json"'},
        )

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
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    run()
