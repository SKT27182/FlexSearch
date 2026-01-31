"""
FlexSearch Backend - Project Schemas

Pydantic models for project endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    """Project creation request."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)


class ProjectUpdate(BaseModel):
    """Project update request."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=2000)


class ProjectResponse(BaseModel):
    """Project response model."""

    id: UUID
    name: str
    description: str | None
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    document_count: int = 0

    class Config:
        from_attributes = True


class ProjectListResponse(BaseModel):
    """List of projects response."""

    projects: list[ProjectResponse]
    total: int
