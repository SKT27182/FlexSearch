"""
FlexSearch Backend - Projects API Router

Project CRUD endpoints.
"""

import io
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_current_active_user, get_db
from app.db.models import Document, DocumentStatus, Project, RagMode, User
from app.schemas.graph_index import (
    GraphIndexState,
    GraphIndexStatusResponse,
    default_graph_index_status,
)
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
    RagModeSwitchRequest,
    RagModeSwitchResponse,
    ReindexRequest,
    ReindexResponse,
    project_to_response,
    resolve_create_rag_config,
    graph_backend_for_project,
)
from app.schemas.rag_config import (
    GraphRagConfig,
    default_rag_config_for_mode,
    parse_rag_config,
)
from app.rag.pipeline import create_pipeline
from app.services.document_tasks import schedule_process_document
from app.services.document_worker import ReindexMode
from app.services.graph_index_tasks import schedule_graph_index_rebuild
from app.services.graphrag_workspace import (
    GRAPHML_CANDIDATES,
    PARQUET_FILES,
    get_graphrag_workspace,
    graphrag_storage_prefix,
)
from app.services.project_access import user_can_access_project, user_owns_project
from app.services.project_index_service import wipe_index_for_mode
from app.services.project_lifecycle import delete_project_fully
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__, level=settings.log_level)

router = APIRouter(prefix="/projects", tags=["projects"])


async def _doc_count(db: AsyncSession, project_id: UUID) -> int:
    q = select(func.count()).select_from(Document).where(Document.project_id == project_id)
    return (await db.execute(q)).scalar() or 0


async def _get_owned_project(
    db: AsyncSession,
    project_id: UUID,
    current_user: User,
) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_owns_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    return project


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    rag_mode = project_data.rag_mode
    rag = resolve_create_rag_config(rag_mode, project_data.rag_config)
    graph_backend = "neo4j"
    if rag_mode == RagMode.GRAPH:
        cfg = project_data.rag_config or default_rag_config_for_mode(RagMode.GRAPH)
        if isinstance(cfg, GraphRagConfig):
            graph_backend = cfg.graph_backend
    project = Project(
        name=project_data.name,
        description=project_data.description,
        owner_id=current_user.id,
        rag_mode=rag_mode,
        rag_config=rag,
        graph_index_status=(
            default_graph_index_status(backend=graph_backend)
            if rag_mode == RagMode.GRAPH
            else None
        ),
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    logger.info("Project created: %s by %s", project.name, current_user.email)
    return project_to_response(project, 0)


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    skip: int = 0,
    limit: int = 100,
) -> ProjectListResponse:
    query = select(Project).where(Project.owner_id == current_user.id)
    query = query.offset(skip).limit(limit).order_by(Project.created_at.desc())
    projects = (await db.execute(query)).scalars().all()

    count_query = (
        select(func.count())
        .select_from(Project)
        .where(Project.owner_id == current_user.id)
    )
    total = (await db.execute(count_query)).scalar() or 0

    responses = []
    for project in projects:
        responses.append(
            project_to_response(project, await _doc_count(db, project.id))
        )
    return ProjectListResponse(projects=responses, total=total)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_can_access_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    return project_to_response(project, await _doc_count(db, project.id))


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_data: ProjectUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ProjectResponse:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_owns_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    if project_data.name is not None:
        project.name = project_data.name
    if project_data.description is not None:
        project.description = project_data.description
    if project_data.rag_config is not None:
        rag_mode = project.rag_mode
        if isinstance(rag_mode, str):
            rag_mode = RagMode(rag_mode)
        parsed = parse_rag_config(rag_mode, project_data.rag_config.to_db())
        project.rag_config = parsed.to_db()

    await db.commit()
    await db.refresh(project)
    return project_to_response(project, await _doc_count(db, project.id))


@router.post("/{project_id}/reindex", response_model=ReindexResponse)
async def reindex_project(
    project_id: UUID,
    body: ReindexRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReindexResponse:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_owns_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    docs_result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.status == DocumentStatus.COMPLETED,
        )
    )
    documents = docs_result.scalars().all()
    mode = ReindexMode(body.mode)
    force = mode == ReindexMode.FULL

    for doc in documents:
        schedule_process_document(
            doc.id,
            project_id,
            force_full_extract=force,
            mode=mode,
        )

    return ReindexResponse(
        processed=len(documents),
        failed=0,
        total_chunks=0,
        message=f"Reindex started for {len(documents)} document(s) in mode={body.mode}",
    )


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not user_owns_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    await delete_project_fully(db, project)
    await db.commit()


@router.get("/{project_id}/graph-index/status", response_model=GraphIndexStatusResponse)
async def get_graph_index_status(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GraphIndexStatusResponse:
    project = await _get_owned_project(db, project_id, current_user)
    if project.rag_mode != RagMode.GRAPH:
        raise HTTPException(
            status_code=400,
            detail="Graph index status is only available for graph projects",
        )
    from app.services.graph_index_tasks import reconcile_stale_graph_index

    await reconcile_stale_graph_index(project_id)
    await db.refresh(project)
    state = GraphIndexState.from_db(project.graph_index_status)
    return GraphIndexStatusResponse(
        backend=state.backend,
        status=state.status,
        indexed_at=state.indexed_at,
        fingerprint=state.fingerprint,
        error=state.error,
        document_count=state.document_count,
        entity_count=state.entity_count,
        passage_count=state.passage_count,
    )


@router.post("/{project_id}/graph-index/rebuild", response_model=GraphIndexStatusResponse)
async def rebuild_graph_index(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GraphIndexStatusResponse:
    project = await _get_owned_project(db, project_id, current_user)
    if project.rag_mode != RagMode.GRAPH:
        raise HTTPException(status_code=400, detail="Project is not in graph mode")
    backend = graph_backend_for_project(project.rag_mode, project.rag_config)
    started = datetime.now(timezone.utc)
    if backend == "microsoft":
        project.graph_index_status = GraphIndexState(
            backend="microsoft",
            status="indexing",
            indexing_started_at=started,
        ).to_db()
        await db.commit()
        schedule_graph_index_rebuild(project_id, debounce_seconds=0.0)
    else:
        project.graph_index_status = GraphIndexState(
            backend="neo4j",
            status="indexing",
            indexing_started_at=started,
            entity_count=0,
            passage_count=0,
        ).to_db()
        await db.commit()
        docs_result = await db.execute(
            select(Document).where(Document.project_id == project_id)
        )
        for doc in docs_result.scalars().all():
            schedule_process_document(
                doc.id,
                project_id,
                force_full_extract=False,
                mode=ReindexMode.FROM_EXTRACTED,
            )
    state = GraphIndexState.from_db(project.graph_index_status)
    return GraphIndexStatusResponse(
        status="indexing" if state.status != "disabled" else state.status,
        indexed_at=state.indexed_at,
        fingerprint=state.fingerprint,
        error=state.error,
        document_count=state.document_count,
        entity_count=state.entity_count,
        passage_count=state.passage_count,
    )


@router.get("/{project_id}/graph-export")
async def export_graph(
    project_id: UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    project = await _get_owned_project(db, project_id, current_user)
    if project.rag_mode != RagMode.GRAPH:
        raise HTTPException(status_code=400, detail="Graph export requires graph mode")
    backend = graph_backend_for_project(project.rag_mode, project.rag_config)
    if backend != "microsoft":
        raise HTTPException(
            status_code=400,
            detail="Graph export is only available for Microsoft GraphRAG projects",
        )
    state = GraphIndexState.from_db(project.graph_index_status)
    if state.status != "ready":
        raise HTTPException(
            status_code=409,
            detail="Graph index is not ready for export",
        )

    storage = get_storage_service()
    prefix = graphrag_storage_prefix(project_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in PARQUET_FILES:
            key = f"{prefix}/output/{name}"
            if storage.file_exists(key):
                zf.writestr(name, storage.download_file(key))
        for candidate in GRAPHML_CANDIDATES:
            key = f"{prefix}/output/{candidate}"
            if storage.file_exists(key):
                zf.writestr(Path(candidate).name, storage.download_file(key))
                break
        else:
            for key in storage.list_files(f"{prefix}/output/"):
                if key.endswith(".graphml"):
                    rel = key.split("/output/", 1)[-1]
                    zf.writestr(Path(rel).name, storage.download_file(key))
                    break

    buffer.seek(0)
    filename = f"graph-export-{project_id}.zip"
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.patch("/{project_id}/rag-mode", response_model=RagModeSwitchResponse)
async def switch_rag_mode(
    project_id: UUID,
    body: RagModeSwitchRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RagModeSwitchResponse:
    project = await _get_owned_project(db, project_id, current_user)
    new_mode = RagMode(body.rag_mode)
    old_mode = project.rag_mode
    old_backend = graph_backend_for_project(old_mode, project.rag_config)
    if new_mode == RagMode.GRAPH:
        new_backend = body.graph_backend or (
            old_backend if new_mode == old_mode else "neo4j"
        )
    else:
        new_backend = old_backend

    if new_mode == old_mode and (
        new_mode != RagMode.GRAPH or new_backend == old_backend
    ):
        return RagModeSwitchResponse(
            rag_mode=new_mode.value,
            message="Project is already in the requested mode",
            documents_queued=0,
        )

    wipe_index_for_mode(
        project_id,
        from_mode=old_mode.value if isinstance(old_mode, RagMode) else str(old_mode),
        graph_backend=old_backend,
    )
    project.rag_mode = new_mode
    if new_mode == RagMode.GRAPH:
        project.rag_config = default_rag_config_for_mode(
            new_mode, graph_backend=new_backend
        ).to_db()
        project.graph_index_status = default_graph_index_status(backend=new_backend)
    else:
        project.rag_config = default_rag_config_for_mode(new_mode).to_db()
        project.graph_index_status = None
    await db.commit()

    docs_result = await db.execute(
        select(Document).where(Document.project_id == project_id)
    )
    documents = docs_result.scalars().all()
    for doc in documents:
        schedule_process_document(
            doc.id,
            project_id,
            force_full_extract=False,
            mode=ReindexMode.AUTO,
        )

    return RagModeSwitchResponse(
        rag_mode=new_mode.value,
        message=(
            f"Switched from {old_mode.value} to {new_mode.value}. "
            "Previous index data was removed; documents are reprocessing."
        ),
        documents_queued=len(documents),
    )
