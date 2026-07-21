"""SearchStore shared types for OpenSearch-backed retrieval."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SummaryLevel = Literal["chunk", "cluster", "document"]


class SearchFilters(BaseModel):
    """Filter predicates applied to search / scroll / delete queries."""

    project_id: str | None = None
    document_id: str | None = None
    rag_generation: int | None = None
    chunk_type: str | None = None
    parent_id: str | None = None
    summary_level: SummaryLevel | None = None
    summary_levels: list[SummaryLevel] | None = None
    cluster_id: str | None = None
    # Neighbor expand (Phase 2 context_window): inclusive chunk_index range
    chunk_index_min: int | None = None
    chunk_index_max: int | None = None


class SearchDocument(BaseModel):
    """Document upserted into the search index."""

    id: str
    embedding: list[float]
    content: str
    project_id: str
    document_id: str
    rag_generation: int = 1
    embedding_model: str = ""
    embedding_dimension: int = 0
    chunk_index: int = 0
    chunk_type: str | None = None
    parent_id: str | None = None
    summary_level: SummaryLevel = "chunk"
    cluster_id: str | None = None
    member_chunk_ids: list[str] = Field(default_factory=list)
    filename: str = ""
    start_char: int | None = None
    end_char: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_source(self) -> dict[str, Any]:
        """Flatten to an OpenSearch `_source` body."""
        source: dict[str, Any] = {
            "embedding": self.embedding,
            "content": self.content,
            "project_id": self.project_id,
            "document_id": self.document_id,
            "rag_generation": self.rag_generation,
            "embedding_model": self.embedding_model,
            "embedding_dimension": self.embedding_dimension,
            "chunk_index": self.chunk_index,
            "summary_level": self.summary_level,
            "filename": self.filename,
        }
        if self.chunk_type is not None:
            source["chunk_type"] = self.chunk_type
        if self.parent_id is not None:
            source["parent_id"] = self.parent_id
        if self.cluster_id is not None:
            source["cluster_id"] = self.cluster_id
        if self.member_chunk_ids:
            source["member_chunk_ids"] = self.member_chunk_ids
        if self.start_char is not None:
            source["start_char"] = self.start_char
        if self.end_char is not None:
            source["end_char"] = self.end_char
        for key, value in self.extra.items():
            if key not in source and value is not None:
                source[key] = value
        return source


class SearchHit(BaseModel):
    """Normalized search / get result."""

    id: str
    score: float = 0.0
    content: str = ""
    project_id: str = ""
    document_id: str = ""
    chunk_index: int = 0
    chunk_type: str | None = None
    parent_id: str | None = None
    summary_level: str = "chunk"
    cluster_id: str | None = None
    member_chunk_ids: list[str] = Field(default_factory=list)
    filename: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_opensearch(cls, hit: dict[str, Any]) -> SearchHit:
        source = hit.get("_source") or {}
        return cls(
            id=str(hit.get("_id", "")),
            score=float(hit.get("_score") or 0.0),
            content=str(source.get("content") or ""),
            project_id=str(source.get("project_id") or ""),
            document_id=str(source.get("document_id") or ""),
            chunk_index=int(source.get("chunk_index") or 0),
            chunk_type=source.get("chunk_type"),
            parent_id=source.get("parent_id"),
            summary_level=str(source.get("summary_level") or "chunk"),
            cluster_id=source.get("cluster_id"),
            member_chunk_ids=list(source.get("member_chunk_ids") or []),
            filename=str(source.get("filename") or ""),
            payload=dict(source),
        )
