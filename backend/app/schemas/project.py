"""
FlexSearch Backend - Project Schemas

Pydantic models for project endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.db.models import RagMode
from app.schemas.graph_index import GraphIndexState, GraphIndexStatusResponse
from app.schemas.rag_config import (
    GraphBackend,
    GraphRagConfig,
    VectorRagConfig,
    default_rag_config_for_mode,
    parse_rag_config,
)


class ProjectCreate(BaseModel):
    """Project creation request."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    rag_mode: RagMode = RagMode.VECTOR
    rag_config: VectorRagConfig | GraphRagConfig | None = None

    @model_validator(mode="after")
    def validate_config_mode(self) -> ProjectCreate:
        if self.rag_config is None:
            return self
        if self.rag_mode == RagMode.VECTOR and not isinstance(
            self.rag_config, VectorRagConfig
        ):
            raise ValueError("Vector projects require vector RAG configuration")
        if self.rag_mode == RagMode.GRAPH and not isinstance(
            self.rag_config, GraphRagConfig
        ):
            raise ValueError("Graph projects require graph RAG configuration")
        return self


class ProjectUpdate(BaseModel):
    """Project update request."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)
    rag_mode: RagMode | None = None
    rag_config: VectorRagConfig | GraphRagConfig | None = None

    @model_validator(mode="after")
    def reject_rag_mode_change(self) -> ProjectUpdate:
        if self.rag_mode is not None:
            raise ValueError("rag_mode cannot be changed after project creation")
        return self


class RagModeSwitchRequest(BaseModel):
    """Destructive switch between vector and graph RAG modes."""

    rag_mode: Literal["vector", "graph"]
    graph_backend: GraphBackend | None = None


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
    rag_mode: RagMode
    rag_config: VectorRagConfig | GraphRagConfig
    graph_index_status: GraphIndexStatusResponse | None = None
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


def _graph_index_response(
    data: dict[str, Any] | None,
) -> GraphIndexStatusResponse | None:
    if not data:
        return None
    state = GraphIndexState.from_db(data)
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


def project_to_response(project: Any, document_count: int = 0) -> ProjectResponse:
    rag_mode = project.rag_mode
    if isinstance(rag_mode, str):
        rag_mode = RagMode(rag_mode)
    rag_config = parse_rag_config(rag_mode, project.rag_config)
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        owner_id=project.owner_id,
        rag_mode=rag_mode,
        rag_config=rag_config,
        graph_index_status=_graph_index_response(project.graph_index_status),
        created_at=project.created_at,
        updated_at=project.updated_at,
        document_count=document_count,
    )


def resolve_create_rag_config(
    rag_mode: RagMode,
    rag_config: VectorRagConfig | GraphRagConfig | None,
) -> dict[str, Any]:
    if rag_config is not None:
        return rag_config.to_db()
    return default_rag_config_for_mode(rag_mode).to_db()


def graph_backend_for_project(
    rag_mode: RagMode,
    rag_config: dict[str, Any] | None,
) -> GraphBackend:
    if rag_mode != RagMode.GRAPH:
        return "neo4j"
    if rag_config and rag_config.get("graph_backend") == "microsoft":
        return "microsoft"
    return "neo4j"
