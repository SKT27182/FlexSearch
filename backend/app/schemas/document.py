"""
FlexSearch Backend - Document Schemas

Pydantic models for document endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    """Document response model."""

    id: UUID
    project_id: UUID
    filename: str
    content_type: str
    file_size: int = Field(..., serialization_alias="size_bytes")
    status: str
    error_message: str | None
    chunk_count: int
    created_at: datetime
    processed_at: datetime | None

    class Config:
        from_attributes = True


class DocumentListResponse(BaseModel):
    """List of documents response."""

    documents: list[DocumentResponse]
    total: int


class DocumentUploadResponse(BaseModel):
    """Document upload response."""

    id: UUID
    filename: str
    status: str
    message: str
