from __future__ import annotations

import logging
import asyncio
import re
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.api import (
    applications,
    auth,
    candidates,
    evaluations,
    fairness,
    final_recommendations,
    interviews,
    requisitions,
    resume_upload,
    screenings,
)
from app.core.config import get_settings
from app.core.logging import configure_logging, correlation_id
from app.core.observability import record_request, render_metrics
from app.database.connection import SessionLocal, init_db
from app.database.models import User


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.validate_startup()
    configure_logging()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    if settings.auto_create_tables:
        init_db()
    with SessionLocal() as db:
        db.execute(select(User.user_id).limit(1))
    for logger in ("openai", "httpx", "httpcore"):
        logging.getLogger(logger).setLevel(logging.WARNING)
    from app.services.screening_retention import retention_worker

    retention_task = asyncio.create_task(retention_worker())
    try:
        yield
    finally:
        retention_task.cancel()
        try:
            await retention_task
        except asyncio.CancelledError:
            pass
    from app.agents.resume_agent import get_resume_agent

    get_resume_agent().close()
    from app.integrations.local_voice import get_voice_runtime

    if get_voice_runtime.cache_info().currsize:
        get_voice_runtime().unload()


app = FastAPI(
    title="Trustworthy HR Hiring Platform",
    version="0.3.0",
    description="Phase 1/2: private resume intake, explainable evidence evaluation and mandatory HR approval. "
    "Phase 3 adds consent-aware local voice screening and mandatory HR review. "
    "Decision support only; no autonomous hiring or real-world invitations.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

from app.api.screening_limits import ScreeningAudioLimit  # noqa: E402

app.add_middleware(ScreeningAudioLimit)


@app.middleware("http")
async def security_and_correlation_headers(request: Request, call_next):
    supplied_id = request.headers.get("X-Request-ID", "")
    request_id = supplied_id if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", supplied_id) else str(uuid.uuid4())
    token = correlation_id.set(request_id)
    try:
        response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        record_request(request.method, route_path, response.status_code)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response
    finally:
        correlation_id.reset(token)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Do not echo request values: validation input can include passwords and private text.
    return JSONResponse(
        status_code=422,
        content={
            "detail": [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        },
    )


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    return JSONResponse(
        status_code=409,
        content={"detail": "The operation conflicts with an existing record. Refresh and retry."},
    )


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, exc: SQLAlchemyError):
    logging.getLogger(__name__).error("Database operation failed: %s", type(exc).__name__)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database temporarily unavailable. No successful operation was confirmed."},
    )


for router in (
    auth.router,
    requisitions.router,
    resume_upload.router,
    applications.router,
    candidates.router,
    evaluations.router,
    screenings.router,
    interviews.router,
    fairness.router,
    final_recommendations.router,
):
    app.include_router(router)


@app.get("/health", tags=["Service"])
def health():
    return {
        "status": "ok",
        "version": "0.2.0",
        "provider": "groq",
        "groq_configured": get_settings().groq_configured,
        "azure_configured": get_settings().azure_configured,
    }


@app.get("/ready", tags=["Service"])
def ready():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/health/live", tags=["Service"])
def health_live():
    return {"status": "live"}


@app.get("/health/ready", tags=["Service"])
def health_ready():
    ready()
    return {"status": "ready"}


@app.get("/metrics", tags=["Service"], response_class=PlainTextResponse)
def metrics():
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4; charset=utf-8")
