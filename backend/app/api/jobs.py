"""Job progress SSE + suggestion endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.documents import verify_project_access
from app.core.dependencies import get_current_active_user, get_db
from app.core.rate_limit import SENSITIVE_RULE, check_rate_limit
from app.db.models import User
from app.services.job_events import get_job_meta, get_last_job_event, job_channel
from app.services.redis_client import get_redis
from app.services.suggestion import (
    generate_followup_questions,
    generate_project_suggestions,
)

router = APIRouter(tags=["jobs", "suggestions"])


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _authorize_job_sse(
    job_id: str,
    current_user: User,
    db: AsyncSession,
) -> None:
    """Require project ownership for the job's project_id when resolvable."""
    meta = await get_job_meta(job_id)
    if meta is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found or expired",
        )
    project_id_raw = meta.get("project_id")
    if not project_id_raw:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Job has no project scope; access denied",
        )
    try:
        project_id = UUID(str(project_id_raw))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid project_id on job metadata",
        ) from exc
    await verify_project_access(project_id, current_user, db)


@router.get("/jobs/{job_id}/events")
async def stream_job_events(
    job_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> StreamingResponse:
    """SSE progress for crawl / bulk Celery jobs (project-scoped ACL)."""
    await _authorize_job_sse(job_id, current_user, db)

    async def event_generator() -> AsyncGenerator[str, None]:
        last = await get_last_job_event(job_id)
        if last:
            yield _format_sse("snapshot", last)
            if last.get("event") in ("complete", "error"):
                yield _format_sse("close", {"reason": "terminal"})
                return

        redis = await get_redis()
        if redis is None:
            # Poll last-event key
            for _ in range(120):
                await asyncio.sleep(1.0)
                ev = await get_last_job_event(job_id)
                if not ev:
                    continue
                yield _format_sse("progress", ev)
                if ev.get("event") in ("complete", "error"):
                    yield _format_sse("close", {"reason": "terminal"})
                    return
            yield _format_sse("error", {"detail": "Redis unavailable / timeout"})
            return

        pubsub = redis.pubsub()
        channel = job_channel(job_id)
        await pubsub.subscribe(channel)
        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=30.0,
                )
                if message is None:
                    await asyncio.sleep(0.3)
                    continue
                if message["type"] != "message":
                    continue
                data = json.loads(message["data"])
                yield _format_sse("progress", data)
                if data.get("event") in ("complete", "error"):
                    yield _format_sse("close", {"reason": "terminal"})
                    break
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class SuggestionsResponse(BaseModel):
    questions: list[str]


@router.get(
    "/projects/{project_id}/suggestions",
    response_model=SuggestionsResponse,
)
async def project_suggestions(
    project_id: UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    count: int = 5,
) -> SuggestionsResponse:
    await check_rate_limit(
        request, SENSITIVE_RULE, user_id=str(current_user.id)
    )
    await verify_project_access(project_id, current_user, db)
    count = max(1, min(count, 10))
    try:
        questions = await generate_project_suggestions(db, project_id, count=count)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SuggestionsResponse(questions=questions)


class FollowupRequest(BaseModel):
    project_id: UUID
    query: str
    answer: str
    count: int = Field(default=3, ge=1, le=8)


@router.post("/chat/suggestions/followup", response_model=SuggestionsResponse)
async def followup_suggestions(
    body: FollowupRequest,
    request: Request,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SuggestionsResponse:
    await check_rate_limit(
        request, SENSITIVE_RULE, user_id=str(current_user.id)
    )
    await verify_project_access(body.project_id, current_user, db)
    try:
        questions = await generate_followup_questions(
            query=body.query,
            answer=body.answer,
            project_id=body.project_id,
            count=body.count,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return SuggestionsResponse(questions=questions)
