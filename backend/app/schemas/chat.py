"""Pydantic schemas for chat query, stream, and history APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.rag_config import RetrievalOverrides


class ChatQueryRequest(BaseModel):
    project_id: str
    query: str = Field(min_length=1)
    session_id: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)
    overrides: RetrievalOverrides | None = None
    persist: bool = True


class ChatCitation(BaseModel):
    index: int
    chunk_id: str
    document_id: str
    content: str
    score: float
    filename: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatQueryResponse(BaseModel):
    project_id: str
    query: str
    answer: str
    citations: list[ChatCitation]
    retrieval_strategy: str
    reranking_strategy: str
    session_id: str | None = None
    turn_id: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    empty_retrieval: bool = False
    grounded: bool = False
    invalid_citations: list[int] = Field(default_factory=list)
    debug: dict[str, Any] | None = None


class ChatSessionCreate(BaseModel):
    project_id: str
    title: str | None = None


class ChatSessionResponse(BaseModel):
    id: str
    project_id: str
    user_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    turn_count: int | None = None


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionResponse]
    total: int


class ChatTurnResponse(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    citations: list[Any] | dict[str, Any] | None = None
    retrieval_strategy: str | None = None
    reranking_strategy: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    created_at: datetime


class ChatTurnListResponse(BaseModel):
    session_id: str
    turns: list[ChatTurnResponse]
