"""
Application entry-point.

Responsibilities (and nothing more):
  - Create the FastAPI app
  - Wire middleware (CORS, request-id)
  - Mount routers
  - Configure structured JSON logging

Reference: ARCHITECTURE.md §2.1 — "No business logic inside main.py."
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from backend.api.routes import router as api_router
from backend.config.settings import get_settings

# ── Structured logging ───────────────────────────────────────────────────────


def _configure_logging() -> None:
    """JSON-formatted structured logging to stdout."""
    settings = get_settings()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    )

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())
    root.handlers.clear()
    root.addHandler(handler)

    # Silence noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


# ── Lifespan ─────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    logger = logging.getLogger(__name__)
    settings = get_settings()
    logger.info(
        "startup",
        extra={
            "app": settings.app_name,
            "version": settings.app_version,
            "debug": settings.debug,
        },
    )
    yield
    logger.info("shutdown")


# ── App factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # -- CORS --
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Request-ID middleware --
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # -- Routers --
    app.include_router(api_router, prefix="/api")

    return app


app = create_app()
