"""
FlexSearch Backend - Analytics Schemas

Pydantic models for usage analytics.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class TokenUsageRecord(BaseModel):
    """Single token usage record."""

    id: UUID
    user_id: UUID
    project_id: UUID
    session_id: str
    model_name: str
    provider: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    created_at: datetime

    class Config:
        from_attributes = True


class UsageSummary(BaseModel):
    """Aggregated usage summary."""

    total_input_tokens: int
    total_output_tokens: int
    total_requests: int
    average_latency_ms: float
    period_start: datetime
    period_end: datetime


class ProjectUsageSummary(BaseModel):
    """Usage summary per project."""

    project_id: UUID
    project_name: str
    total_input_tokens: int
    total_output_tokens: int
    total_requests: int


class UserUsageSummary(BaseModel):
    """Usage summary per user."""

    user_id: UUID
    email: str
    total_input_tokens: int
    total_output_tokens: int
    total_requests: int


class UsageAnalyticsResponse(BaseModel):
    """Full analytics response."""

    summary: UsageSummary
    by_project: list[ProjectUsageSummary]
    by_user: list[UserUsageSummary] | None = None  # Admin only
