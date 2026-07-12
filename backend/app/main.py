"""
FlexSearch Backend - FastAPI Application

Main entry point for the RAG platform API.
"""

import signal
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logger import create_logger
from app.utils.logging_bridge import (
    configure_third_party_loggers,
    patch_graphrag_init_loggers,
    setup_unified_logging,
)

from app.api import admin, auth, bulk, chat, documents, jobs, projects, rag, retrieval, website
from app.core.config import settings
from app.core.dependencies import get_db
from app.core.security import verify_password
from app.db.postgres import close_db, init_db
from app.services.neo4j_store import get_neo4j_store
from app.services.redis_client import close_redis
from app.db.models import User

logger = create_logger(__name__, level=settings.log_level)

# Route GraphRAG/LiteLLM/SQLAlchemy/uvicorn into the colored + file logging stack.
# setup_unified_logging already calls configure_third_party_loggers; re-apply after
# import so a late uvicorn dictConfig cannot leave plain white handlers behind.
_log_file = setup_unified_logging(settings.log_level)
configure_third_party_loggers(settings.log_level)
patch_graphrag_init_loggers()
logger.info("Unified logging enabled (backend log: %s)", _log_file)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""
    # Re-apply after uvicorn's own logging setup (CLI / reload workers).
    configure_third_party_loggers(settings.log_level)
    # Startup
    logger.info("Starting FlexSearch Backend...")
    await init_db()
    logger.info("Database initialized")
    try:
        get_neo4j_store().ensure_schema()
        logger.info("Neo4j schema ensured")
    except Exception as exc:
        logger.warning("Neo4j schema bootstrap skipped: %s", exc)
    try:
        from app.services.graph_index_tasks import (
            reconcile_interrupted_graph_indexes,
        )

        await reconcile_interrupted_graph_indexes()
    except Exception as exc:
        logger.warning("Graph index reconciliation skipped: %s", exc)
    yield
    # Shutdown
    logger.info("Shutting down FlexSearch Backend...")
    await close_redis()
    await close_db()
    logger.info("Cleanup complete")


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


# Setup signal handlers
setup_signal_handlers()


# Create FastAPI app
app = FastAPI(
    title="FlexSearch RAG Platform",
    description="High-Performance, Local-First Modular RAG Platform",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

docs_security = HTTPBasic()


async def get_docs_auth(
    credentials: HTTPBasicCredentials = Depends(docs_security),
    db: AsyncSession = Depends(get_db),
) -> str:
    result = await db.execute(select(User).where(User.email == credentials.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api")
app.include_router(projects.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(website.router, prefix="/api")
app.include_router(bulk.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(retrieval.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(rag.router, prefix="/api")
app.include_router(admin.router, prefix="/api")


@app.get("/")
async def root() -> dict:
    """Root endpoint."""
    return {
        "name": "FlexSearch RAG Platform",
        "version": "0.1.0",
        "status": "running",
    }


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint (includes OpenSearch + Redis smoke status)."""
    services: dict[str, str] = {}
    try:
        from app.services.search_store import get_search_store
        from app.services.search_store.opensearch_store import OpenSearchStore

        store = get_search_store()
        if isinstance(store, OpenSearchStore) and store.ping():
            services["opensearch"] = "ok"
        else:
            services["opensearch"] = "unreachable"
    except Exception as exc:
        services["opensearch"] = f"error: {exc}"

    try:
        from app.services.redis_client import get_redis

        redis = await get_redis()
        if redis is None:
            services["redis"] = "unreachable"
        else:
            pong = await redis.ping()
            services["redis"] = "ok" if pong else "unreachable"
    except Exception as exc:
        services["redis"] = f"error: {exc}"

    healthy = services.get("opensearch") == "ok" and services.get("redis") == "ok"
    payload: dict = {
        "status": "healthy" if healthy else "degraded",
        "services": services,
    }
    if settings.metrics_enabled:
        from app.observability.metrics import metrics

        payload["metrics"] = metrics.snapshot()
    return payload


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus text exposition of in-process FlexSearch metrics."""
    from fastapi.responses import PlainTextResponse

    if not settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics disabled")
    from app.observability.metrics import metrics

    return PlainTextResponse(
        metrics.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


@app.get("/api/docs", include_in_schema=False)
async def api_docs(_: str = Depends(get_docs_auth)):
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title="FlexSearch API Docs",
    )


@app.get("/api/openapi.json", include_in_schema=False)
async def api_openapi(_: str = Depends(get_docs_auth)):
    return get_openapi(title=app.title, version=app.version, routes=app.routes)


@app.get("/docs", include_in_schema=False)
async def docs_redirect() -> RedirectResponse:
    return RedirectResponse(url="/api/docs")


@app.get("/openapi.json", include_in_schema=False)
async def openapi_redirect() -> RedirectResponse:
    return RedirectResponse(url="/api/openapi.json")


if __name__ == "__main__":
    import uvicorn

    # log_config=None: keep our colored handlers; do not let uvicorn dictConfig
    # reinstall plain white formatters.
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=settings.debug,
        log_config=None,
    )
