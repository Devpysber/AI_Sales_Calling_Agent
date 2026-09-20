"""
FastAPI application: REST API under /api, Plivo webhooks, and (when a
frontend build exists) the dashboard SPA.
"""

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import agents, calls, intake, knowledge, leads, plivo, system
from app.core import auth
from app.core.config import settings
from app.core.database import run_migrations
from app.core.logging import get_logger, setup_logging

setup_logging(settings.log_level, json_logs=settings.is_production)
log = get_logger("app")
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def production_problems() -> list[str]:
    """Settings that are unsafe or break scaling in production (only enforced when ENVIRONMENT=production)."""
    if not settings.is_production:
        return []
    import os

    from app.core.auth import auth_enabled
    problems = []
    if not settings.secret_key or len(settings.secret_key) < 32:
        problems.append("SECRET_KEY must be set (32+ characters) and identical on every replica")
    if not auth_enabled():
        problems.append("ADMIN_PASSWORD must be set (or a password set on the Admin profile)")
    if not settings.public_base_url.startswith("https://"):
        problems.append("PUBLIC_BASE_URL must be an https:// URL Plivo can reach")
    if int(os.environ.get("WEB_CONCURRENCY", "1")) > 1 and not settings.redis_url:
        problems.append("REDIS_URL is required with more than one worker (call state is shared in Redis)")
    if settings.database_url.startswith("sqlite") and settings.redis_url:
        problems.append("Use PostgreSQL (DATABASE_URL) when running several replicas; SQLite is single-host only")
    if settings.plivo_auth_id and not settings.plivo_validate_signature:
        problems.append("PLIVO_VALIDATE_SIGNATURE must be true in production")
    return problems


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.run_migrations:
        await asyncio.to_thread(run_migrations)
        from app.services import agents
        from app.services.crm_service import CRMService
        first = (await asyncio.to_thread(agents.ids) or [None])[0]
        await asyncio.to_thread(CRMService(first).import_legacy_excel, settings.legacy_excel_file)

    problems = production_problems()
    if problems:
        for problem in problems:
            log.error("Production check failed: %s", problem)
        if settings.is_production:
            raise RuntimeError("Refusing to start in production: " + "; ".join(problems))

    task = None
    if settings.run_scheduler:
        from app.services.scheduler import scheduler_loop
        task = asyncio.create_task(scheduler_loop())
    log.info("%s %s started (%s)", settings.app_name, settings.app_version, settings.environment)
    
    # Warm up TTS in background so startup isn't blocked, but it's ready quickly
    from app.services import tts, llm
    asyncio.create_task(asyncio.to_thread(tts.warmup))
    # Same for embeddings: the first OpenRouter round trip is ~1.7s (TLS + pool) against ~0.5s warm, and a
    # live turn only waits 0.3s for one, so a cold first call would run its opening turns without knowledge.
    asyncio.create_task(asyncio.to_thread(llm.embed, ["warm-up"], 10))

    yield
    if task:
        task.cancel()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)

app.add_middleware(GZipMiddleware, minimum_size=1024)
if settings.cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(","), allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
app.middleware("http")(auth.auth_middleware)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        log.exception("Unhandled error on %s %s", request.method, request.url.path, extra={"request_id": request_id})
        response = JSONResponse({"detail": "Internal server error", "request_id": request_id}, status_code=500)
    response.headers["x-request-id"] = request_id
    elapsed = (time.perf_counter() - started) * 1000
    if request.url.path.startswith("/api/") and elapsed > 1500:
        log.warning("Slow request %s %s %.0fms", request.method, request.url.path, elapsed, extra={"request_id": request_id})
    return response



for module in (auth, agents, leads, calls, knowledge, system, plivo, intake):
    app.include_router(module.router)
app.include_router(intake.public)


# ---------------- SPA (single-container / local mode; nginx serves it in production) ----------------

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        file = FRONTEND_DIST / path
        if path and file.is_file():
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html", headers={"Cache-Control": "no-cache"})
