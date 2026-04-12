"""
FlexSearch Backend - Retrieval Schemas

Pydantic models for retrieval-only query endpoints.
"""

from typing import Any

from pydantic import BaseModel, Field


class RetrievalQueryRequest(BaseModel):
    """Retrieval query request."""

    project_id: str
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)


class RetrievedChunk(BaseModel):
    """Retrieved chunk with metadata."""

    chunk_id: str
    document_id: str
    content: str
    score: float
    metadata: dict[str, Any]


class RetrievalQueryResponse(BaseModel):
    """Retrieval query response."""

    project_id: str
    query: str
    retrieval_strategy: str
    total: int
    chunks: list[RetrievedChunk]
