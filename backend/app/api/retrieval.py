"""
FlexSearch Backend - Retrieval API Router

Retrieval-only query endpoint returning chunks and metadata.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_db
from app.db.models import Project, User
from app.rag.pipeline import get_rag_pipeline
from app.schemas.retrieval import (
    RetrievedChunk,
    RetrievalQueryRequest,
    RetrievalQueryResponse,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)

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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if project.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this project",
        )

    rag_pipeline = get_rag_pipeline()
    results = await rag_pipeline.retrieve(
        query=request.query,
        project_id=request.project_id,
        top_k=request.top_k,
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

    logger.info(
        f"Retrieval query served: project={request.project_id}, "
        f"user={current_user.email}, results={len(chunks)}"
    )

    return RetrievalQueryResponse(
        project_id=request.project_id,
        query=request.query,
        retrieval_strategy=rag_pipeline.retrieval_strategy,
        total=len(chunks),
        chunks=chunks,
    )
