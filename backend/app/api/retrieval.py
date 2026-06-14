"""
FlexSearch Backend - Retrieval API Router

Retrieval-only query endpoint returning chunks and metadata.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.dependencies import get_current_active_user, get_db
from app.db.models import Project, User, RagMode
from app.rag.pipeline import get_rag_pipeline
from app.schemas.graph_index import GraphIndexState
from app.schemas.rag_config import RagConfig
from app.schemas.retrieval import (
    RetrievedChunk,
    RetrievalQueryRequest,
    RetrievalQueryResponse,
)
from app.services.project_access import user_can_access_project
from app.services.retrieval_validation import validate_retrieval_for_mode
from app.utils.logger import create_logger

logger = create_logger(__name__, level=settings.log_level)

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.post("/query", response_model=RetrievalQueryResponse)
async def query_retrieval(
    request: RetrievalQueryRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RetrievalQueryResponse:
    """Retrieve relevant chunks for a project query."""
    try:
        project_uuid = UUID(request.project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project ID format",
        )

    result = await db.execute(select(Project).where(Project.id == project_uuid))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if not user_can_access_project(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    rag_config = RagConfig.from_db(project.rag_config)
    validation_error = validate_retrieval_for_mode(
        project.rag_mode, rag_config, request.overrides
    )
    if validation_error:
        raise HTTPException(status_code=400, detail=validation_error)

    if project.rag_mode == RagMode.GRAPH:
        graph_state = GraphIndexState.from_db(project.graph_index)
        if graph_state.status != "ready":
            raise HTTPException(
                status_code=409,
                detail="Graph index is not ready. Wait for indexing to complete.",
            )

    pipeline = get_rag_pipeline(rag_config)
    results, retrieval_name, rerank_name = await pipeline.retrieve(
        query=request.query,
        project_id=request.project_id,
        top_k=request.top_k,
        overrides=request.overrides,
    )

    chunks = [
        RetrievedChunk(
            chunk_id=result.chunk_id,
            document_id=result.document_id,
            content=result.content,
            score=result.score,
            metadata=result.metadata,
        )
        for result in results
    ]

    return RetrievalQueryResponse(
        project_id=request.project_id,
        query=request.query,
        retrieval_strategy=retrieval_name,
        reranking_strategy=rerank_name,
        total=len(chunks),
        chunks=chunks,
    )
