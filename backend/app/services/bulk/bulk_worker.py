"""Bulk import/export workers."""

from __future__ import annotations

import shutil
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from app.db.models import Document, DocumentStatus, Project, RagMode
from app.db.postgres import async_session_maker
from app.schemas.rag_config import default_rag_config_for_mode
from app.services.bulk.ragpack import (
    build_ragpack_zip,
    find_manifest_dir,
    load_manifest,
    safe_extract_zip,
    safe_join,
    validate_referenced_files,
)
from app.services.bulk.schemas import (
    FileDocumentReference,
    TextDocumentReference,
    UrlDocumentReference,
)
from app.services.job_events import publish_job_event
from app.services.storage import get_storage_service
from app.services.text_document import (
    create_and_enqueue_document,
    guess_content_type,
)
from app.services.website.content_extractor import extract_clean_content
from app.utils.logger import create_logger

logger = create_logger(__name__)


async def run_bulk_import_job(
    job_id: str,
    *,
    archive_bytes: bytes | None = None,
    archive_path: str | None = None,
    target_project_id: UUID | None = None,
    owner_user_id: UUID | None = None,
) -> dict:
    """
    Import a .ragpack into ``target_project_id`` (preferred) or create projects
    from the manifest (requires ``owner_user_id``).
    """
    await publish_job_event(
        job_id,
        {
            "event": "progress",
            "stage": "validating",
            "message": "Validating .ragpack…",
            "progress": 5,
        },
    )

    extract_dir = tempfile.mkdtemp(prefix="ragpack_")
    tmp_zip: str | None = None
    try:
        zip_path = archive_path
        if archive_bytes is not None:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            tmp.write(archive_bytes)
            tmp.close()
            tmp_zip = tmp.name
            zip_path = tmp_zip

        if not zip_path:
            raise ValueError("No archive provided")

        with zipfile.ZipFile(zip_path, "r") as zf:
            safe_extract_zip(zf, extract_dir)

        base_dir = find_manifest_dir(Path(extract_dir))
        manifest = load_manifest(base_dir)
        validate_referenced_files(manifest, base_dir)

        total_docs = sum(len(p.documents) for p in manifest.projects)
        await publish_job_event(
            job_id,
            {
                "event": "progress",
                "stage": "extracting",
                "message": f"Found {len(manifest.projects)} project(s), {total_docs} document(s)",
                "progress": 10,
            },
        )

        documents_succeeded = 0
        documents_failed = 0
        doc_ids: list[str] = []
        processed = 0

        async with async_session_maker() as db:
            for project_def in manifest.projects:
                project_id = target_project_id
                if project_id is None:
                    if owner_user_id is None:
                        raise ValueError("target_project_id or owner_user_id required")
                    project = Project(
                        name=project_def.name,
                        description=project_def.description,
                        owner_id=owner_user_id,
                        rag_mode=RagMode.VECTOR,
                        rag_config=default_rag_config_for_mode(
                            RagMode.VECTOR
                        ).model_dump(),
                    )
                    db.add(project)
                    await db.commit()
                    await db.refresh(project)
                    project_id = project.id
                else:
                    result = await db.execute(
                        select(Project).where(Project.id == project_id)
                    )
                    if result.scalar_one_or_none() is None:
                        raise ValueError(f"Project not found: {project_id}")

                for doc_ref in project_def.documents:
                    processed += 1
                    progress = 10 + int(80 * processed / max(total_docs, 1))
                    try:
                        filename, data, ctype = await _resolve_document(
                            doc_ref, base_dir
                        )
                        document = await create_and_enqueue_document(
                            db,
                            project_id=project_id,
                            filename=filename,
                            data=data,
                            content_type=ctype,
                        )
                        documents_succeeded += 1
                        doc_ids.append(str(document.id))
                        await publish_job_event(
                            job_id,
                            {
                                "event": "document_complete",
                                "stage": "ingesting",
                                "message": f"Queued: {filename}",
                                "progress": min(progress, 95),
                                "document_id": str(document.id),
                                "project_id": str(project_id),
                            },
                        )
                    except Exception as exc:
                        documents_failed += 1
                        logger.error("Bulk import doc failed: %s", exc)
                        await publish_job_event(
                            job_id,
                            {
                                "event": "progress",
                                "stage": "ingesting",
                                "message": f"Failed: {exc}",
                                "progress": min(progress, 95),
                            },
                        )

        result = {
            "event": "complete",
            "stage": "complete",
            "message": "Bulk import complete",
            "progress": 100,
            "documents_succeeded": documents_succeeded,
            "documents_failed": documents_failed,
            "document_ids": doc_ids,
            "project_id": str(target_project_id) if target_project_id else None,
        }
        await publish_job_event(job_id, result)
        return result

    except Exception as exc:
        logger.exception("Bulk import failed job=%s: %s", job_id, exc)
        await publish_job_event(
            job_id,
            {
                "event": "error",
                "stage": "error",
                "message": str(exc),
                "progress": 0,
            },
        )
        raise
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)
        if archive_bytes is not None and tmp_zip:
            Path(tmp_zip).unlink(missing_ok=True)


async def _resolve_document(doc_ref, base_dir: Path) -> tuple[str, bytes, str]:
    if isinstance(doc_ref, UrlDocumentReference):
        from app.core.config import settings
        from app.services.url_safety import UnsafeURLError, assert_public_url

        url = str(doc_ref.url)
        if settings.crawl_block_private_urls:
            try:
                assert_public_url(url)
            except UnsafeURLError as exc:
                raise ValueError(f"Unsafe URL in ragpack: {exc}") from exc
        from app.services.safe_http import SafeOutboundHttpClient

        async with SafeOutboundHttpClient(timeout=60) as client:
            resp = await client.get(url)
            # Manual redirect follow with SSRF re-check
            hops = 0
            while resp.is_redirect and hops < 5:
                location = resp.headers.get("location")
                if not location:
                    break
                next_url = str(httpx.URL(url).join(location))
                if settings.crawl_block_private_urls:
                    assert_public_url(next_url)
                url = next_url
                resp = await client.get(url)
                hops += 1
            resp.raise_for_status()
            url_path = url.split("?")[0]
            filename = doc_ref.title or Path(url_path).name or "download.bin"
            if doc_ref.title and not Path(filename).suffix:
                filename = f"{filename}{Path(url_path).suffix or '.bin'}"
            ctype = resp.headers.get("content-type", "").split(";")[0].strip()
            if not ctype or ctype == "application/octet-stream":
                ctype = guess_content_type(filename)
            data = resp.content
            if "text/html" in ctype:
                md = extract_clean_content(data.decode("utf-8", errors="replace"))
                return (
                    f"{Path(filename).stem}.md",
                    md.encode("utf-8"),
                    "text/markdown",
                )
            return filename, data, ctype

    if isinstance(doc_ref, (FileDocumentReference, TextDocumentReference)):
        path = safe_join(base_dir, doc_ref.path)
        data = path.read_bytes()
        filename = doc_ref.title or path.name
        if isinstance(doc_ref, TextDocumentReference):
            ctype = doc_ref.content_type
        else:
            ctype = guess_content_type(path.name)
        if ctype == "text/html" or path.suffix.lower() in {".html", ".htm"}:
            md = extract_clean_content(data.decode("utf-8", errors="replace"))
            return f"{Path(filename).stem}.md", md.encode("utf-8"), "text/markdown"
        if not Path(filename).suffix:
            filename = path.name
        return filename, data, ctype

    raise ValueError(f"Unsupported document reference: {doc_ref}")


async def export_project_ragpack(project_id: UUID) -> bytes:
    """Export completed documents' extracted text (or raw) as .ragpack.zip."""
    storage = get_storage_service()
    async with async_session_maker() as db:
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            raise ValueError("Project not found")

        docs_result = await db.execute(
            select(Document)
            .where(Document.project_id == project_id)
            .where(Document.status == DocumentStatus.COMPLETED)
            .order_by(Document.created_at.asc())
        )
        documents = list(docs_result.scalars().all())
        if not documents:
            raise ValueError("No completed documents to export")

        files: list[tuple[str, bytes]] = []
        for doc in documents:
            safe = "".join(
                c if c.isalnum() or c in ("-", "_", ".") else "_"
                for c in (doc.filename or str(doc.id))
            )
            content: bytes | None = None
            out_name = safe
            if doc.extracted_text_path and storage.file_exists(doc.extracted_text_path):
                content = storage.download_file(doc.extracted_text_path)
                if not out_name.lower().endswith((".md", ".txt")):
                    out_name = f"{Path(out_name).stem}.md"
            elif doc.storage_path and storage.file_exists(doc.storage_path):
                content = storage.download_file(doc.storage_path)
            if content is None:
                logger.warning("Skipping document %s — no content in storage", doc.id)
                continue
            # Avoid collisions
            archive_path = f"documents/{doc.id}_{out_name}"
            files.append((archive_path, content))

        if not files:
            raise ValueError("No document content available for export")

        return build_ragpack_zip(
            project_name=project.name,
            description=project.description,
            files=files,
        )
