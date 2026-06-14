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
from app.db.models import Project, RagMode, User
from app.rag.pipeline import create_pipeline
from app.schemas.graph_index import GraphIndexState
from app.schemas.project import graph_backend_for_project
from app.schemas.rag_config import (
    GRAPH_RETRIEVAL_STRATEGIES,
    VECTOR_RETRIEVAL_STRATEGIES,
    parse_rag_config,
)
from app.schemas.retrieval import (
    RetrievedChunk,
    RetrievalQueryRequest,
    RetrievalQueryResponse,
)
from app.services.neo4j_store import Neo4jStoreError, get_neo4j_store
from app.services.project_access import user_can_access_project
from app.services.retrieval_validation import validate_retrieval_for_mode
from app.utils.logger import create_logger

logger = create_logger(__name__, level=settings.log_level)

router = APIRouter(prefix="/retrieval", tags=["retrieval"])


def _validate_strategy_for_mode(rag_mode: RagMode, strategy: str) -> None:
    if rag_mode == RagMode.GRAPH:
        if strategy not in GRAPH_RETRIEVAL_STRATEGIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Strategy '{strategy}' is not valid for graph projects",
            )
    elif strategy not in VECTOR_RETRIEVAL_STRATEGIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Strategy '{strategy}' is not valid for vector projects",
        )


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

    rag_mode = project.rag_mode
    if isinstance(rag_mode, str):
        rag_mode = RagMode(rag_mode)

    rag_config = parse_rag_config(rag_mode, project.rag_config)
    validation_error = validate_retrieval_for_mode(
        rag_mode, rag_config, request.overrides
    )
    if validation_error:
        raise HTTPException(status_code=400, detail=validation_error)

    effective_strategy = rag_config.retrieval.strategy
    if request.overrides and request.overrides.retrieval_strategy is not None:
        effective_strategy = request.overrides.retrieval_strategy
    _validate_strategy_for_mode(rag_mode, effective_strategy)

    if rag_mode == RagMode.GRAPH:
        backend = graph_backend_for_project(rag_mode, project.rag_config)
        if backend == "microsoft":
            graph_state = GraphIndexState.from_db(project.graph_index_status)
            if graph_state.status != "ready":
                raise HTTPException(
                    status_code=409,
                    detail="Graph index is not ready. Wait for indexing to complete.",
                )
        else:
            try:
                stats = get_neo4j_store().get_stats(request.project_id)
            except Neo4jStoreError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc
            if stats.passage_count == 0 and stats.entity_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Graph index not ready — upload and process documents first",
                )

    pipeline = create_pipeline(rag_config, rag_mode=rag_mode)
    try:
        results, retrieval_name, rerank_name = await pipeline.retrieve(
            query=request.query,
            project_id=request.project_id,
            top_k=request.top_k,
            overrides=request.overrides,
        )
    except Neo4jStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

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
