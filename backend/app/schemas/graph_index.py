"""Graph index status helpers and schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

GraphIndexStatusValue = Literal["pending", "indexing", "ready", "failed", "disabled"]
GraphBackend = Literal["neo4j", "microsoft"]


class GraphIndexState(BaseModel):
    backend: GraphBackend | None = None
    status: GraphIndexStatusValue = "pending"
    indexed_at: datetime | str | None = None
    fingerprint: str | None = None
    error: str | None = None
    document_count: int | None = None
    entity_count: int | None = None
    passage_count: int | None = None

    @classmethod
    def from_db(cls, data: dict[str, Any] | None) -> GraphIndexState:
        if not data:
            return cls()
        return cls.model_validate(data)

    def to_db(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class GraphIndexStatusResponse(BaseModel):
    backend: GraphBackend | None = None
    status: GraphIndexStatusValue
    indexed_at: datetime | str | None = None
    fingerprint: str | None = None
    error: str | None = None
    document_count: int | None = None
    entity_count: int | None = None
    passage_count: int | None = None


def default_graph_index_status(
    *,
    backend: GraphBackend = "neo4j",
) -> dict[str, Any]:
    if backend == "microsoft":
        return GraphIndexState(backend="microsoft", status="pending").to_db()
    return GraphIndexState(
        backend="neo4j",
        status="pending",
        entity_count=0,
        passage_count=0,
    ).to_db()
