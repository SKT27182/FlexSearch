"""
FlexSearch Backend - Session Schemas

Pydantic models for session and chat endpoints.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    """Session creation request."""

    project_id: str
    title: str | None = None


class SessionResponse(BaseModel):
    """Session response model."""

    id: str
    project_id: str
    user_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    """List of sessions response."""

    sessions: list[SessionResponse]
    total: int


class ChatMessage(BaseModel):
    """Chat message model."""

    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    """Chat request model."""

    message: str
    session_id: str
    project_id: str


class ChatResponse(BaseModel):
    """Chat response model."""

    message: str
    sources: list[dict] = []
    session_id: str
