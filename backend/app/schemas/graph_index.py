"""Graph index status helpers and schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

GraphIndexStatus = Literal["pending", "indexing", "ready", "failed", "disabled"]


class GraphIndexState(BaseModel):
    status: GraphIndexStatus = "pending"
    indexed_at: datetime | None = None
    fingerprint: str | None = None
    error: str | None = None
    document_count: int | None = None

    @classmethod
    def from_db(cls, data: dict[str, Any] | None) -> GraphIndexState:
        if not data:
            return cls()
        return cls.model_validate(data)

    def to_db(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        return payload


class GraphIndexStatusResponse(BaseModel):
    status: GraphIndexStatus
    indexed_at: datetime | None = None
    fingerprint: str | None = None
    error: str | None = None
    document_count: int | None = None


def default_graph_index() -> dict[str, Any]:
    return GraphIndexState().to_db()
