"""Background document ingestion worker."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus, Project
from app.db.postgres import async_session_maker
from app.rag.pipeline import create_pipeline
from app.schemas.rag_config import RagConfig, extraction_fingerprint
from app.services.document_status import update_document_status
from app.services.document_storage import (
    build_extracted_meta,
    extracted_md_key,
    extracted_meta_key,
    meta_to_bytes,
)
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__)

CONTENT_PREVIEW_MAX = 500_000


class ReindexMode(str, Enum):
    AUTO = "auto"
    FULL = "full"
    FROM_EXTRACTED = "from_extracted"


async def get_project_rag_config(db: AsyncSession, project_id: UUID) -> RagConfig:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    return RagConfig.from_db(project.rag_config)


async def process_document(
    document_id: UUID,
    project_id: UUID,
    *,
    force_full_extract: bool = False,
    mode: ReindexMode = ReindexMode.AUTO,
) -> None:
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
            return

        rag_config = await get_project_rag_config(db, project_id)
        pipeline = create_pipeline(rag_config)
        storage = get_storage_service()
        ext_hash = extraction_fingerprint(rag_config.extraction)

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
                return

            content_format = (
                "markdown" if rag_config.extraction.strategy == "vlm" else "plain"
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

            await _run_chunk_and_index(
                db, document, pipeline, storage, rag_config, ext_hash, extracted.text, extracted.page_count
            )

        except Exception as exc:
            logger.exception("Document processing failed: %s", document_id)
            await update_document_status(
                db,
                document,
                status=DocumentStatus.FAILED,
                processing_step="Failed",
                progress_pct=0,
                error_message=str(exc),
            )


async def _run_chunk_and_index(
    db: AsyncSession,
    document: Document,
    pipeline,
    storage,
    rag_config: RagConfig,
    ext_hash: str,
    text: str | None = None,
    page_count: int = 0,
) -> None:
    pipeline.delete_document_data(str(document.id))

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
        processing_step="Indexing vectors in Qdrant…",
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
