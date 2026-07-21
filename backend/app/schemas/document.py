"""
FlexSearch Backend - Document Schemas

Pydantic models for document endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentResponse(BaseModel):
    """Document response model."""

    id: UUID
    project_id: UUID
    filename: str
    content_type: str
    file_size: int = Field(..., serialization_alias="size_bytes")
    status: str
    processing_step: str | None = None
    progress_pct: int = 0
    error_message: str | None
    chunk_count: int
    created_at: datetime
    processed_at: datetime | None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class DocumentListResponse(BaseModel):
    """List of documents response."""

    documents: list[DocumentResponse]
    total: int


class DocumentContentResponse(BaseModel):
    """Extracted markdown/text content."""

    document_id: UUID
    content: str
    content_type: str = "text/markdown; charset=utf-8"
    truncated: bool = False


class DocumentUploadResponse(BaseModel):
    """Document upload response."""

    id: UUID
    filename: str
    status: str
    message: str
