"""
FlexSearch Celery application (app-owned workers on infra-hub Redis).

Queues: ingest | graph | summary | default
Broker/backend: REDIS_URL (same Redis used for SSE progress).
"""

from __future__ import annotations

import asyncio

from celery import Celery
from celery.signals import beat_init, worker_init

from app.core.config import settings

celery_app = Celery(
    "flexsearch",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.services.celery_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    task_default_queue="default",
    task_create_missing_queues=True,
    task_routes={
        "app.services.celery_tasks.dispatch_outbox_task": {"queue": "default"},
        "app.services.celery_tasks.process_document_task": {"queue": "ingest"},
        "app.services.celery_tasks.rebuild_graph_index_task": {"queue": "graph"},
        "app.services.celery_tasks.build_document_summaries_task": {"queue": "summary"},
        "app.services.celery_tasks.website_crawl_task": {"queue": "default"},
        "app.services.celery_tasks.bulk_import_task": {"queue": "default"},
    },
    beat_schedule={
        "dispatch-outbox": {
            "task": "app.services.celery_tasks.dispatch_outbox_task",
            "schedule": 5.0,
        }
    },
)


def _verify_database_revision(**_: object) -> None:
    from app.db.postgres import engine, init_db

    async def verify_and_dispose() -> None:
        try:
            await init_db()
        finally:
            # Celery worker_init and beat_init may run in the same process.
            # Never leave asyncpg connections bound to asyncio.run()'s closed loop.
            await engine.dispose()

    asyncio.run(verify_and_dispose())


worker_init.connect(_verify_database_revision, weak=False)
beat_init.connect(_verify_database_revision, weak=False)

# Alias for `celery -A app.celery_app worker`
app = celery_app
