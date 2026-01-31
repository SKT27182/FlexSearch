"""
FlexSearch Backend - Analytics API Router

Token usage analytics and reporting endpoints.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_db, require_admin
from app.db.models import Project, TokenUsage, User
from app.schemas.analytics import (
    ProjectUsageSummary,
    TokenUsageRecord,
    UsageAnalyticsResponse,
    UsageSummary,
    UserUsageSummary,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/usage", response_model=UsageAnalyticsResponse)
async def get_usage_analytics(
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=30, ge=1, le=365),
) -> UsageAnalyticsResponse:
    """
    Get usage analytics for the current user.

    Returns summary and per-project breakdown for the specified period.
    """
    period_start = datetime.now(timezone.utc) - timedelta(days=days)
    period_end = datetime.now(timezone.utc)

    # Get user's projects
    project_result = await db.execute(
        select(Project).where(Project.owner_id == current_user.id)
    )
    projects = {p.id: p for p in project_result.scalars().all()}
    project_ids = list(projects.keys())

    if not project_ids:
        return UsageAnalyticsResponse(
            summary=UsageSummary(
                total_input_tokens=0,
                total_output_tokens=0,
                total_requests=0,
                average_latency_ms=0.0,
                period_start=period_start,
                period_end=period_end,
            ),
            by_project=[],
        )

    # Overall summary
    summary_result = await db.execute(
        select(
            func.sum(TokenUsage.input_tokens).label("total_input"),
            func.sum(TokenUsage.output_tokens).label("total_output"),
            func.count(TokenUsage.id).label("total_requests"),
            func.avg(TokenUsage.latency_ms).label("avg_latency"),
        ).where(
            TokenUsage.project_id.in_(project_ids),
            TokenUsage.created_at >= period_start,
        )
    )
    summary = summary_result.first()

    # Per-project breakdown
    project_result = await db.execute(
        select(
            TokenUsage.project_id,
            func.sum(TokenUsage.input_tokens).label("total_input"),
            func.sum(TokenUsage.output_tokens).label("total_output"),
            func.count(TokenUsage.id).label("total_requests"),
        )
        .where(
            TokenUsage.project_id.in_(project_ids),
            TokenUsage.created_at >= period_start,
        )
        .group_by(TokenUsage.project_id)
    )

    by_project = [
        ProjectUsageSummary(
            project_id=row.project_id,
            project_name=projects[row.project_id].name,
            total_input_tokens=row.total_input or 0,
            total_output_tokens=row.total_output or 0,
            total_requests=row.total_requests or 0,
        )
        for row in project_result
    ]

    return UsageAnalyticsResponse(
        summary=UsageSummary(
            total_input_tokens=summary.total_input or 0,
            total_output_tokens=summary.total_output or 0,
            total_requests=summary.total_requests or 0,
            average_latency_ms=float(summary.avg_latency or 0),
            period_start=period_start,
            period_end=period_end,
        ),
        by_project=by_project,
    )


@router.get("/usage/admin", response_model=UsageAnalyticsResponse)
async def get_admin_usage_analytics(
    _: Annotated[User, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
    days: int = Query(default=30, ge=1, le=365),
) -> UsageAnalyticsResponse:
    """
    Get system-wide usage analytics (admin only).

    Returns summary, per-project, and per-user breakdown.
    """
    period_start = datetime.now(timezone.utc) - timedelta(days=days)
    period_end = datetime.now(timezone.utc)

    # Overall summary
    summary_result = await db.execute(
        select(
            func.sum(TokenUsage.input_tokens).label("total_input"),
            func.sum(TokenUsage.output_tokens).label("total_output"),
            func.count(TokenUsage.id).label("total_requests"),
            func.avg(TokenUsage.latency_ms).label("avg_latency"),
        ).where(TokenUsage.created_at >= period_start)
    )
    summary = summary_result.first()

    # Per-project breakdown
    project_result = await db.execute(
        select(
            TokenUsage.project_id,
            func.sum(TokenUsage.input_tokens).label("total_input"),
            func.sum(TokenUsage.output_tokens).label("total_output"),
            func.count(TokenUsage.id).label("total_requests"),
        )
        .where(TokenUsage.created_at >= period_start)
        .group_by(TokenUsage.project_id)
    )

    # Get project names
    project_ids = [row.project_id for row in project_result]
    projects_query = await db.execute(
        select(Project).where(Project.id.in_(project_ids))
    )
    projects = {p.id: p for p in projects_query.scalars().all()}

    by_project = [
        ProjectUsageSummary(
            project_id=row.project_id,
            project_name=projects.get(
                row.project_id, type("", (), {"name": "Unknown"})()
            ).name,
            total_input_tokens=row.total_input or 0,
            total_output_tokens=row.total_output or 0,
            total_requests=row.total_requests or 0,
        )
        for row in project_result
    ]

    # Per-user breakdown
    user_result = await db.execute(
        select(
            TokenUsage.user_id,
            func.sum(TokenUsage.input_tokens).label("total_input"),
            func.sum(TokenUsage.output_tokens).label("total_output"),
            func.count(TokenUsage.id).label("total_requests"),
        )
        .where(TokenUsage.created_at >= period_start)
        .group_by(TokenUsage.user_id)
    )

    # Get user emails
    user_ids = [row.user_id for row in user_result]
    users_query = await db.execute(select(User).where(User.id.in_(user_ids)))
    users = {u.id: u for u in users_query.scalars().all()}

    by_user = [
        UserUsageSummary(
            user_id=row.user_id,
            email=users.get(row.user_id, type("", (), {"email": "Unknown"})()).email,
            total_input_tokens=row.total_input or 0,
            total_output_tokens=row.total_output or 0,
            total_requests=row.total_requests or 0,
        )
        for row in user_result
    ]

    return UsageAnalyticsResponse(
        summary=UsageSummary(
            total_input_tokens=summary.total_input or 0,
            total_output_tokens=summary.total_output or 0,
            total_requests=summary.total_requests or 0,
            average_latency_ms=float(summary.avg_latency or 0),
            period_start=period_start,
            period_end=period_end,
        ),
        by_project=by_project,
        by_user=by_user,
    )
