"""Bulk .ragpack import/export API."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import verify_project_access
from app.core.dependencies import get_current_active_user, get_db
from app.core.rate_limit import BULK_RULE, check_rate_limit
from app.db.models import User
from app.services.bulk.bulk_tasks import schedule_bulk_import
from app.services.bulk.bulk_worker import export_project_ragpack
from app.services.bulk.schemas import BulkImportSubmitResponse
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(tags=["bulk"])


def _is_ragpack_name(filename: str) -> bool:
    lower = filename.lower()
    return (
        lower.endswith(".ragpack")
        or lower.endswith(".ragpack.zip")
        or lower.endswith(".zip")
    )


@router.post(
    "/projects/{project_id}/bulk-import",
    response_model=BulkImportSubmitResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_bulk_import(
    project_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File(...)],
) -> BulkImportSubmitResponse:
    """Upload a .ragpack and enqueue Celery import into this project."""
    await check_rate_limit(request, BULK_RULE, user_id=str(current_user.id))
    await verify_project_access(project_id, current_user, db)
    if not file.filename or not _is_ragpack_name(file.filename):
        raise HTTPException(
            status_code=400,
            detail="Expected .ragpack, .ragpack.zip, or .zip",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    storage = get_storage_service()
    storage_path = f"{project_id}/imports/{file.filename}"
    storage.upload_file(
        path=storage_path,
        data=content,
        content_type="application/zip",
    )
    job_id = schedule_bulk_import(
        storage_path=storage_path,
        target_project_id=project_id,
        owner_user_id=current_user.id,
    )
    return BulkImportSubmitResponse(
        job_id=job_id,
        status="queued",
        project_id=str(project_id),
    )


@router.get("/projects/{project_id}/export")
async def export_project(
    project_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """Download project documents as a .ragpack.zip."""
    await check_rate_limit(request, BULK_RULE, user_id=str(current_user.id))
    project = await verify_project_access(project_id, current_user, db)
    try:
        zip_bytes = await export_project_ragpack(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Export failed for %s", project_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    safe = re.sub(r"[^a-zA-Z0-9_\- ]", "", project.name or "project").strip()
    safe = safe.replace(" ", "_") or "project"
    filename = f"{safe}.ragpack.zip"
    return StreamingResponse(
        BytesIO(zip_bytes),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(zip_bytes)),
        },
    )
