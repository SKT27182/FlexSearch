"""Background document ingestion worker."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Document, DocumentStatus, Project, RagMode
from app.db.postgres import async_session_maker
from app.observability.metrics import metrics
from app.rag.graph.indexer import GraphIndexer
from app.rag.ingestion.preprocess import preprocess_extracted_text
from app.rag.pipeline import create_pipeline
from app.schemas.rag_config import (
    GraphRagConfig,
    VectorRagConfig,
    extraction_fingerprint,
    parse_rag_config,
)
from app.services.document_status import update_document_status
from app.services.graph_index_tasks import schedule_graph_index_rebuild
from app.services.document_storage import (
    build_extracted_meta,
    extracted_md_key,
    extracted_meta_key,
    meta_to_bytes,
)
from app.services.neo4j_store import Neo4jStoreError, get_neo4j_store
from app.services.storage import get_storage_service
from app.services.summary_tasks import schedule_document_summary, cancel_document_summary
from app.utils.logger import create_logger

logger = create_logger(__name__)

CONTENT_PREVIEW_MAX = 500_000


class DocumentIngestError(Exception):
    """Ingest failed after document status was updated (or document missing).

    Raised so Celery marks the task as FAILURE instead of returning success.
    """


class ReindexMode(str, Enum):
    AUTO = "auto"
    FULL = "full"
    FROM_EXTRACTED = "from_extracted"


async def get_project_rag_context(
    db: AsyncSession, project_id: UUID
) -> tuple[RagMode, VectorRagConfig | GraphRagConfig, Project]:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    rag_mode = project.rag_mode
    if isinstance(rag_mode, str):
        rag_mode = RagMode(rag_mode)
    config = parse_rag_config(rag_mode, project.rag_config)
    return rag_mode, config, project


async def get_project_rag_config(
    db: AsyncSession, project_id: UUID
) -> VectorRagConfig | GraphRagConfig:
    _, config, _ = await get_project_rag_context(db, project_id)
    return config


async def _update_graph_index_status(
    db: AsyncSession,
    project: Project,
    *,
    status: str,
    error: str | None = None,
) -> None:
    stats = get_neo4j_store().get_stats(str(project.id))
    now = datetime.now(timezone.utc).isoformat()
    payload: dict[str, Any] = {
        "status": status,
        "indexed_at": now,
        "entity_count": stats.entity_count,
        "passage_count": stats.passage_count,
        "error": error,
        "fingerprint": (
            parse_rag_config(project.rag_mode, project.rag_config).ingestion_fingerprint()
            if project.rag_config
            else None
        ),
    }
    if status == "indexing":
        payload["indexing_started_at"] = now
    project.graph_index_status = payload
    await db.commit()


def _is_microsoft_graph(config: GraphRagConfig) -> bool:
    return config.graph_backend == "microsoft"


async def _handle_graph_after_extract(
    db: AsyncSession,
    document: Document,
    project: Project,
    rag_config: GraphRagConfig,
    storage,
    ext_hash: str,
    *,
    text: str | None = None,
    page_count: int = 0,
) -> None:
    if _is_microsoft_graph(rag_config):
        await _complete_graph_document(db, document)
        # Microsoft GraphRAG indexes the whole project at once, so wait until
        # every document in the project has reached a terminal state before
        # scheduling a rebuild. Otherwise each finished doc kicks off a build
        # and they overlap (the debounce cancel does not stop an in-flight
        # worker). Only the last document to finish triggers a single rebuild.
        pending = await _count_non_terminal_documents(db, project.id)
        if pending:
            logger.info(
                "Graph rebuild deferred for project %s: %d document(s) still processing",
                project.id,
                pending,
            )
            return
        logger.info(
            "All documents ready for project %s; scheduling graph rebuild",
            project.id,
        )
        schedule_graph_index_rebuild(project.id)
        return
    await _run_graph_index(
        db,
        document,
        project,
        rag_config,
        storage,
        ext_hash,
        text,
        page_count,
    )


async def _count_non_terminal_documents(
    db: AsyncSession, project_id: UUID
) -> int:
    """Count documents not yet COMPLETED or FAILED (still being processed)."""
    from sqlalchemy import func

    terminal = (DocumentStatus.COMPLETED, DocumentStatus.FAILED)
    result = await db.execute(
        select(func.count())
        .select_from(Document)
        .where(
            Document.project_id == project_id,
            Document.status.notin_(terminal),
        )
    )
    return int(result.scalar() or 0)


async def _safe_fail_document(
    db: AsyncSession,
    document: Document,
    project: Project,
    *,
    processing_step: str,
    error_message: str,
    mark_graph_failed: bool = False,
) -> None:
    """Best-effort FAILED status; ignore if the row was deleted mid-ingest."""
    if mark_graph_failed:
        try:
            await _update_graph_index_status(
                db, project, status="failed", error=error_message
            )
        except Exception:
            logger.debug(
                "Could not mark graph index failed for project=%s",
                project.id,
                exc_info=True,
            )
    updated = await update_document_status(
        db,
        document,
        status=DocumentStatus.FAILED,
        processing_step=processing_step,
        progress_pct=0,
        error_message=error_message,
    )
    if not updated:
        logger.info(
            "Document %s gone during fail update (likely deleted); skipping status write",
            document.id,
        )


async def process_document(
    document_id: UUID,
    project_id: UUID,
    *,
    force_full_extract: bool = False,
    mode: ReindexMode = ReindexMode.AUTO,
) -> None:
    import time

    ingest_started = time.perf_counter()
    async with async_session_maker() as db:
        result = await db.execute(
            select(Document).where(
                Document.id == document_id,
                Document.project_id == project_id,
            )
        )
        document = result.scalar_one_or_none()
        if not document:
            logger.error("Document %s not found", document_id)
            metrics.record_ingest(status="missing")
            raise DocumentIngestError(f"Document not found: {document_id}")

        rag_mode, rag_config, project = await get_project_rag_context(db, project_id)
        pipeline = create_pipeline(rag_config, rag_mode=rag_mode)
        storage = get_storage_service()
        ext_hash = extraction_fingerprint(rag_config.extraction)

        if rag_mode == RagMode.GRAPH and not settings.api_key:
            await update_document_status(
                db,
                document,
                status=DocumentStatus.FAILED,
                processing_step="Graph RAG requires LLM API key",
                progress_pct=0,
                error_message="Set API_KEY in backend/.env for Graph RAG indexing",
            )
            metrics.record_ingest(
                status="failed",
                seconds=time.perf_counter() - ingest_started,
            )
            raise DocumentIngestError(
                "Graph RAG requires LLM API key (set API_KEY in backend/.env)"
            )

        try:
            md_key = extracted_md_key(project_id, document_id)
            can_skip_extract = False
            if mode == ReindexMode.FROM_EXTRACTED:
                if not document.extracted_text_path or not storage.file_exists(
                    document.extracted_text_path
                ):
                    raise ValueError("No extracted.md available for from_extracted mode")
                can_skip_extract = True
            elif mode == ReindexMode.AUTO and not force_full_extract:
                if (
                    document.extracted_text_path
                    and document.extraction_config_hash == ext_hash
                    and storage.file_exists(document.extracted_text_path)
                ):
                    can_skip_extract = True

            if can_skip_extract:
                if rag_mode == RagMode.GRAPH:
                    assert isinstance(rag_config, GraphRagConfig)
                    await _handle_graph_after_extract(
                        db, document, project, rag_config, storage, ext_hash
                    )
                else:
                    await _run_chunk_and_index(
                        db, document, pipeline, storage, rag_config, ext_hash
                    )
                return

            await update_document_status(
                db,
                document,
                status=DocumentStatus.EXTRACTING,
                processing_step="Extracting text from document…",
                progress_pct=40,
                clear_error=True,
            )

            if not storage.file_exists(document.storage_path):
                raise FileNotFoundError(
                    f"Raw file missing: {document.storage_path}"
                )

            raw = storage.download_file(document.storage_path)

            async def on_extract_progress(
                step: str,
                current: int | None,
                total: int | None,
            ) -> None:
                pct = 40
                if current is not None and total and total > 0:
                    pct = 40 + int(15 * current / total)
                await update_document_status(
                    db,
                    document,
                    status=DocumentStatus.EXTRACTING,
                    processing_step=step,
                    progress_pct=pct,
                )

            extracted = await pipeline.extract_document(
                raw,
                document.content_type,
                document.filename,
                on_progress=on_extract_progress,
            )

            if extracted.is_empty:
                await update_document_status(
                    db,
                    document,
                    status=DocumentStatus.FAILED,
                    processing_step="No text extracted",
                    progress_pct=0,
                    error_message="No text could be extracted from this file",
                )
                raise DocumentIngestError(
                    "No text could be extracted from this file"
                )

            # Post-extract preprocess (ftfy / whitespace / headers)
            preprocess_cfg = getattr(rag_config.extraction, "preprocess", None)
            if preprocess_cfg is None or preprocess_cfg.enabled:
                extracted.text = preprocess_extracted_text(
                    extracted.text,
                    fix_encoding=True if preprocess_cfg is None else preprocess_cfg.fix_encoding,
                    normalize_ws=(
                        True
                        if preprocess_cfg is None
                        else preprocess_cfg.normalize_whitespace
                    ),
                    strip_headers_footers=(
                        True
                        if preprocess_cfg is None
                        else preprocess_cfg.strip_headers_footers
                    ),
                )

            content_format = (
                "markdown"
                if rag_config.extraction.strategy in {"vlm", "docling"}
                else "plain"
            )
            text_bytes = extracted.text.encode("utf-8")
            storage.upload_file(
                path=md_key,
                data=text_bytes,
                content_type="text/markdown; charset=utf-8",
            )
            meta = build_extracted_meta(
                content_format=content_format,
                extraction_strategy=rag_config.extraction.strategy,
                page_count=extracted.page_count,
                extraction_config_hash=ext_hash,
                content_type=document.content_type,
            )
            storage.upload_file(
                path=extracted_meta_key(project_id, document_id),
                data=meta_to_bytes(meta),
                content_type="application/json",
            )

            await update_document_status(
                db,
                document,
                status=DocumentStatus.EXTRACTED,
                processing_step="Text ready",
                progress_pct=55,
                extracted_text_path=md_key,
                extraction_config_hash=ext_hash,
                extracted_at=datetime.now(timezone.utc),
            )

            if rag_mode == RagMode.GRAPH:
                assert isinstance(rag_config, GraphRagConfig)
                await _handle_graph_after_extract(
                    db,
                    document,
                    project,
                    rag_config,
                    storage,
                    ext_hash,
                    text=extracted.text,
                    page_count=extracted.page_count,
                )
            else:
                await _run_chunk_and_index(
                    db,
                    document,
                    pipeline,
                    storage,
                    rag_config,
                    ext_hash,
                    extracted.text,
                    extracted.page_count,
                )

        except Neo4jStoreError as exc:
            logger.exception("Neo4j error processing document %s", document_id)
            await _safe_fail_document(
                db,
                document,
                project,
                processing_step="Neo4j unavailable",
                error_message=str(exc),
                mark_graph_failed=True,
            )
            metrics.record_ingest(
                status="failed",
                seconds=time.perf_counter() - ingest_started,
            )
            raise DocumentIngestError(str(exc)) from exc
        except DocumentIngestError:
            raise
        except Exception as exc:
            logger.exception("Document processing failed: %s", document_id)
            await _safe_fail_document(
                db,
                document,
                project,
                processing_step="Failed",
                error_message=str(exc),
                mark_graph_failed=(rag_mode == RagMode.GRAPH),
            )
            metrics.record_ingest(
                status="failed",
                seconds=time.perf_counter() - ingest_started,
            )
            raise DocumentIngestError(str(exc)) from exc

        metrics.record_ingest(
            status="completed",
            seconds=time.perf_counter() - ingest_started,
        )


async def _complete_graph_document(db: AsyncSession, document: Document) -> None:
    await update_document_status(
        db,
        document,
        status=DocumentStatus.COMPLETED,
        processing_step="Text extracted — graph index will rebuild shortly",
        progress_pct=100,
        chunk_count=0,
    )


async def _run_chunk_and_index(
    db: AsyncSession,
    document: Document,
    pipeline,
    storage,
    rag_config: VectorRagConfig,
    ext_hash: str,
    text: str | None = None,
    page_count: int = 0,
) -> None:
    # Stop in-flight summary before wipe so it cannot re-upsert stale docs,
    # and so schedule_document_summary at the end is not blocked.
    cancel_document_summary(document.id)

    pipeline.delete_document_data(
        str(document.id), project_id=str(document.project_id)
    )

    if text is None:
        path = document.extracted_text_path or extracted_md_key(
            document.project_id, document.id
        )
        if not storage.file_exists(path):
            raise FileNotFoundError(f"Extracted text missing: {path}")
        text = storage.download_file(path).decode("utf-8")

    await update_document_status(
        db,
        document,
        status=DocumentStatus.CHUNKING,
        processing_step="Splitting text into chunks…",
        progress_pct=70,
    )

    chunk_count = await pipeline.ingest_from_text(
        text,
        str(document.id),
        str(document.project_id),
        document.filename,
        page_count,
    )

    await update_document_status(
        db,
        document,
        status=DocumentStatus.INDEXING,
        processing_step="Indexing vectors in OpenSearch…",
        progress_pct=85,
    )

    await update_document_status(
        db,
        document,
        status=DocumentStatus.COMPLETED,
        processing_step="Done",
        progress_pct=100,
        chunk_count=chunk_count,
    )

    # Hierarchical summaries (vector only; skip if disabled)
    if isinstance(rag_config, VectorRagConfig) and rag_config.summaries.enabled:
        schedule_document_summary(document.id, document.project_id)


async def _run_graph_index(
    db: AsyncSession,
    document: Document,
    project: Project,
    rag_config: GraphRagConfig,
    storage,
    ext_hash: str,
    text: str | None = None,
    page_count: int = 0,
) -> None:
    del ext_hash, page_count
    if text is None:
        path = document.extracted_text_path or extracted_md_key(
            document.project_id, document.id
        )
        if not storage.file_exists(path):
            raise FileNotFoundError(f"Extracted text missing: {path}")
        text = storage.download_file(path).decode("utf-8")

    await update_document_status(
        db,
        document,
        status=DocumentStatus.GRAPH_INDEXING,
        processing_step="Extracting entities and indexing graph…",
        progress_pct=75,
        clear_error=True,
    )
    await _update_graph_index_status(db, project, status="indexing")

    indexer = GraphIndexer()
    stats = await indexer.index_document(
        str(project.id),
        str(document.id),
        document.filename,
        text,
        rag_config,
    )

    await _update_graph_index_status(db, project, status="ready")

    updated = await update_document_status(
        db,
        document,
        status=DocumentStatus.COMPLETED,
        processing_step="Graph indexed",
        progress_pct=100,
        chunk_count=stats.passage_count,
    )
    if not updated:
        # Deleted while indexing — subgraph already wiped by delete API.
        logger.info(
            "Document %s deleted during graph index; not marking completed",
            document.id,
        )
        return
