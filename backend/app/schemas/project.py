"""
FlexSearch Backend - Project Schemas

Pydantic models for project endpoints.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.db.models import RagMode
from app.schemas.graph_index import GraphIndexState, GraphIndexStatusResponse
from app.schemas.rag_config import RagConfig


class ProjectCreate(BaseModel):
    """Project creation request."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    rag_mode: Literal["vector", "graph"] = "vector"
    rag_config: RagConfig | None = None


class ProjectUpdate(BaseModel):
    """Project update request."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    rag_config: RagConfig | None = None


class RagModeSwitchRequest(BaseModel):
    """Destructive switch between vector and graph RAG modes."""

    rag_mode: Literal["vector", "graph"]


class RagModeSwitchResponse(BaseModel):
    rag_mode: Literal["vector", "graph"]
    message: str
    documents_queued: int


class ProjectResponse(BaseModel):
    """Project response model."""

    id: UUID
    name: str
    description: str | None
    owner_id: UUID
    rag_mode: Literal["vector", "graph"]
    rag_config: RagConfig
    graph_index: GraphIndexStatusResponse
    created_at: datetime
    updated_at: datetime
    document_count: int = 0

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    """List of projects response."""

    projects: list[ProjectResponse]
    total: int


class ReindexRequest(BaseModel):
    mode: str = Field(default="auto", pattern="^(auto|full|from_extracted)$")


class ReindexResponse(BaseModel):
    processed: int
    failed: int
    total_chunks: int
    message: str


def project_to_response(project: Any, document_count: int = 0) -> ProjectResponse:
    graph_index = GraphIndexState.from_db(getattr(project, "graph_index", None) or {})
    rag_mode = getattr(project, "rag_mode", RagMode.VECTOR)
    mode_value = rag_mode.value if isinstance(rag_mode, RagMode) else str(rag_mode)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        owner_id=project.owner_id,
        rag_mode=mode_value,  # type: ignore[arg-type]
        rag_config=RagConfig.from_db(project.rag_config),
        graph_index=GraphIndexStatusResponse(
            status=graph_index.status,
            indexed_at=graph_index.indexed_at,
            fingerprint=graph_index.fingerprint,
            error=graph_index.error,
            document_count=graph_index.document_count,
        ),
        created_at=project.created_at,
        updated_at=project.updated_at,
        document_count=document_count,
    )
