"""Transactional outbox producer and dispatcher."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus, OutboxEvent, OutboxState, Project
from app.db.postgres import async_session_maker
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__)


def add_outbox_event(
    db: AsyncSession,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: UUID,
    project_id: UUID | None,
    payload: dict,
) -> OutboxEvent:
    event = OutboxEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        project_id=project_id,
        payload=payload,
    )
    db.add(event)
    return event


async def dispatch_pending_events(limit: int = 50) -> int:
    """Dispatch a bounded batch; duplicate execution is safe by aggregate task IDs."""
    dispatched = 0
    async with async_session_maker() as db:
        stale_result = await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.state == OutboxState.PROCESSING)
            .where(
                OutboxEvent.locked_at
                < datetime.now(timezone.utc) - timedelta(minutes=5)
            )
            .with_for_update(skip_locked=True)
        )
        for stale in stale_result.scalars().all():
            stale.state = OutboxState.PENDING
            stale.locked_at = None
        await db.commit()
        result = await db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.state == OutboxState.PENDING)
            .where(OutboxEvent.available_at <= datetime.now(timezone.utc))
            .order_by(OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        events = list(result.scalars().all())
        for event in events:
            event.state = OutboxState.PROCESSING
            event.locked_at = datetime.now(timezone.utc)
            event.attempts += 1
        await db.commit()

        for event in events:
            try:
                await _dispatch_event(db, event)
                event.state = OutboxState.COMPLETED
                event.completed_at = datetime.now(timezone.utc)
                event.last_error = None
                dispatched += 1
            except Exception as exc:
                logger.exception("Outbox event %s failed", event.id)
                event.last_error = str(exc)[:2000]
                event.state = (
                    OutboxState.FAILED if event.attempts >= 10 else OutboxState.PENDING
                )
                if event.state == OutboxState.PENDING:
                    event.available_at = datetime.now(timezone.utc) + timedelta(
                        seconds=min(300, 2 ** min(event.attempts, 8))
                    )
                event.locked_at = None
            await db.commit()
    return dispatched


async def _dispatch_event(db: AsyncSession, event: OutboxEvent) -> None:
    if event.event_type == "finalize_upload":
        storage = get_storage_service()
        await asyncio.to_thread(
            storage.promote_file,
            event.payload["temporary_path"],
            event.payload["final_path"],
        )
        result = await db.execute(
            select(Document).where(Document.id == event.aggregate_id)
        )
        document = result.scalar_one()
        document.status = DocumentStatus.STORED
        document.processing_step = "Saved to storage"
        document.progress_pct = 25
        add_outbox_event(
            db,
            event_type="process_document",
            aggregate_type="document",
            aggregate_id=document.id,
            project_id=document.project_id,
            payload={"generation": event.payload["generation"]},
        )
        return
    if event.event_type == "process_document":
        result = await db.execute(
            select(Document.status).where(
                Document.id == event.aggregate_id,
                Document.project_id == event.project_id,
            )
        )
        status = result.scalar_one_or_none()
        if status is None or status == DocumentStatus.DELETING:
            logger.info(
                "Skipping stale ingest event for missing/deleting document %s",
                event.aggregate_id,
            )
            return
        from app.services.document_tasks import schedule_process_document
        from app.services.document_worker import ReindexMode

        schedule_process_document(
            event.aggregate_id,
            event.project_id,
            mode=ReindexMode(event.payload.get("mode", ReindexMode.AUTO.value)),
            force_full_extract=bool(event.payload.get("force_full_extract", False)),
            generation=int(event.payload["generation"]),
        )
        return
    if event.event_type == "rag_mode_rebuild":
        result = await db.execute(
            select(Document).where(Document.project_id == event.project_id)
        )
        documents = list(result.scalars().all())
        for document in documents:
            add_outbox_event(
                db,
                event_type="process_document",
                aggregate_type="document",
                aggregate_id=document.id,
                project_id=event.project_id,
                payload={"generation": event.payload["generation"]},
            )
        if not documents:
            project_result = await db.execute(
                select(Project).where(Project.id == event.project_id)
            )
            project = project_result.scalar_one()
            project.rag_transition_status = "ready"
            project.rag_transition_error = None
            add_outbox_event(
                db,
                event_type="cleanup_previous_index",
                aggregate_type="project",
                aggregate_id=project.id,
                project_id=project.id,
                payload={"generation": project.rag_generation},
            )
        return
    if event.event_type == "cleanup_document":
        result = await db.execute(
            select(Document).where(Document.id == event.aggregate_id)
        )
        document = result.scalar_one_or_none()
        if document is None:
            return
        project_result = await db.execute(
            select(Project).where(Project.id == document.project_id)
        )
        project = project_result.scalar_one()
        from app.rag.pipeline import create_pipeline
        from app.schemas.rag_config import parse_rag_config
        from app.services.document_storage import extracted_md_key, extracted_meta_key
        from app.services.document_tasks import cancel_document_ingest
        from app.services.summary_tasks import cancel_document_summary

        config = parse_rag_config(project.rag_mode, project.rag_config)
        is_microsoft_graph = getattr(config, "graph_backend", None) == "microsoft"

        def cleanup() -> None:
            storage = get_storage_service()
            for path in (
                document.storage_path,
                document.extracted_text_path,
                extracted_md_key(document.project_id, document.id),
                extracted_meta_key(document.project_id, document.id),
            ):
                if path and storage.file_exists(path):
                    storage.delete_file(path)
            cancel_document_ingest(document.id)
            cancel_document_summary(document.id)
            if not is_microsoft_graph:
                create_pipeline(
                    config,
                    rag_mode=project.rag_mode,
                    rag_generation=project.rag_generation,
                ).delete_document_data(
                    str(document.id), project_id=str(document.project_id)
                )

        await asyncio.to_thread(cleanup)
        await db.delete(document)
        if is_microsoft_graph:
            from app.services.graph_index_tasks import (
                mark_microsoft_graph_index_dirty,
            )

            mark_microsoft_graph_index_dirty(project)
        return
    if event.event_type == "cleanup_previous_index":
        result = await db.execute(select(Project).where(Project.id == event.project_id))
        project = result.scalar_one_or_none()
        if project is None:
            return
        generation = int(event.payload["generation"])
        if (
            project.rag_generation != generation
            or project.rag_transition_status != "ready"
        ):
            return

        def cleanup_previous() -> None:
            if project.rag_previous_mode == "vector":
                from app.services.search_store import get_search_store

                get_search_store().delete_old_project_generations(
                    str(project.id), generation
                )
            elif project.rag_previous_mode == "graph":
                if project.rag_previous_backend == "microsoft":
                    previous_generation = project.rag_previous_generation
                    if previous_generation is not None:
                        get_storage_service().delete_prefix(
                            f"projects/{project.id}/graphrag/generations/"
                            f"{previous_generation}"
                        )
                else:
                    from app.services.neo4j_store import get_neo4j_store

                    get_neo4j_store().delete_project_subgraph(str(project.id))

        await asyncio.to_thread(cleanup_previous)
        project.rag_previous_mode = None
        project.rag_previous_backend = None
        project.rag_previous_generation = None
        return
    if event.event_type == "cleanup_project":
        result = await db.execute(
            select(Project).where(Project.id == event.aggregate_id)
        )
        project = result.scalar_one_or_none()
        if project is None:
            return
        documents_result = await db.execute(
            select(Document).where(Document.project_id == project.id)
        )
        documents = list(documents_result.scalars().all())

        def cleanup_project() -> None:
            from app.services.document_tasks import cancel_document_ingest
            from app.services.neo4j_store import get_neo4j_store
            from app.services.search_store import get_search_store
            from app.services.summary_tasks import cancel_document_summary

            for document in documents:
                cancel_document_ingest(document.id)
                cancel_document_summary(document.id)
            storage = get_storage_service()
            storage.delete_prefix(f"{project.id}/")
            storage.delete_prefix(f"projects/{project.id}/")
            get_search_store().delete_by_project(str(project.id))
            get_neo4j_store().delete_project_subgraph(str(project.id))

        await asyncio.to_thread(cleanup_project)
        await db.delete(project)
        return
    if event.event_type == "website_crawl":
        from app.services.website.crawl_tasks import schedule_website_crawl

        schedule_website_crawl(
            event.project_id,
            event.payload["url"],
            max_depth=event.payload.get("max_depth"),
            max_pages=event.payload.get("max_pages"),
            exclude_patterns=event.payload.get("exclude_patterns"),
            respect_robots=event.payload.get("respect_robots"),
            use_sitemap=event.payload.get("use_sitemap"),
            rate_limit=event.payload.get("rate_limit"),
            job_id=event.payload["job_id"],
        )
        return
    if event.event_type == "bulk_import":
        from app.services.bulk.bulk_tasks import schedule_bulk_import

        storage = get_storage_service()
        await asyncio.to_thread(
            storage.promote_file,
            event.payload["temporary_path"],
            event.payload["storage_path"],
        )
        schedule_bulk_import(
            storage_path=event.payload["storage_path"],
            target_project_id=event.project_id,
            owner_user_id=UUID(event.payload["owner_user_id"]),
            job_id=event.payload["job_id"],
        )
        return
    raise ValueError(f"Unsupported outbox event type: {event.event_type}")
