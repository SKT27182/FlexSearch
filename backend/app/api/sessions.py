"""
FlexSearch Backend - Sessions API Router

Session management endpoints.
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_db
from app.db.models import Project, User
from app.db.redis import RedisSessionStore, get_redis
from app.schemas.session import SessionCreate, SessionListResponse, SessionResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])

# Session key patterns
SESSION_KEY = "session:{session_id}"
USER_SESSIONS_KEY = "user:{user_id}:sessions"
SESSION_MESSAGES_KEY = "session:{session_id}:messages"


async def get_session_store() -> RedisSessionStore:
    """Get Redis session store."""
    redis = await get_redis()
    return RedisSessionStore(redis)


@router.post("/", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    session_data: SessionCreate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SessionResponse:
    """Create a new chat session."""
    # Verify project access
    result = await db.execute(
        select(Project).where(Project.id == session_data.project_id)
    )
    project = result.scalar_one_or_none()

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    if project.owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this project",
        )

    # Create session
    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    session_info = {
        "id": session_id,
        "project_id": session_data.project_id,
        "user_id": str(current_user.id),
        "title": session_data.title or "New Chat",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }

    store = await get_session_store()

    # Store session info
    await store.set(
        SESSION_KEY.format(session_id=session_id),
        json.dumps(session_info),
        expire_seconds=86400 * 7,  # 7 days TTL
    )

    # Add to user's session list (sorted set by timestamp)
    await store.zadd(
        USER_SESSIONS_KEY.format(user_id=str(current_user.id)),
        {session_id: now.timestamp()},
    )

    logger.info(f"Session created: {session_id} for user {current_user.email}")

    return SessionResponse(
        id=session_id,
        project_id=session_data.project_id,
        user_id=str(current_user.id),
        title=session_info["title"],
        created_at=now,
        updated_at=now,
    )


@router.get("/", response_model=SessionListResponse)
async def list_sessions(
    current_user: Annotated[User, Depends(get_current_active_user)],
    limit: int = 50,
) -> SessionListResponse:
    """List all sessions for the current user."""
    store = await get_session_store()

    # Get session IDs sorted by time (newest first)
    session_ids = await store.zrange(
        USER_SESSIONS_KEY.format(user_id=str(current_user.id)),
        0,
        limit - 1,
        desc=True,
    )

    sessions = []
    for session_id in session_ids:
        session_data = await store.get(SESSION_KEY.format(session_id=session_id))
        if session_data:
            info = json.loads(session_data)
            sessions.append(
                SessionResponse(
                    id=info["id"],
                    project_id=info["project_id"],
                    user_id=info["user_id"],
                    title=info.get("title"),
                    created_at=datetime.fromisoformat(info["created_at"]),
                    updated_at=datetime.fromisoformat(info["updated_at"]),
                )
            )

    return SessionListResponse(sessions=sessions, total=len(sessions))


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> SessionResponse:
    """Get a specific session."""
    store = await get_session_store()

    session_data = await store.get(SESSION_KEY.format(session_id=session_id))

    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    info = json.loads(session_data)

    # Verify ownership
    if info["user_id"] != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to access this session",
        )

    return SessionResponse(
        id=info["id"],
        project_id=info["project_id"],
        user_id=info["user_id"],
        title=info.get("title"),
        created_at=datetime.fromisoformat(info["created_at"]),
        updated_at=datetime.fromisoformat(info["updated_at"]),
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> None:
    """Delete a session."""
    store = await get_session_store()
    redis = await get_redis()

    session_data = await store.get(SESSION_KEY.format(session_id=session_id))

    if not session_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    info = json.loads(session_data)

    if info["user_id"] != str(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this session",
        )

    # Delete session data
    await store.delete(SESSION_KEY.format(session_id=session_id))
    await store.delete(SESSION_MESSAGES_KEY.format(session_id=session_id))

    # Remove from user's session list
    await redis.zrem(
        USER_SESSIONS_KEY.format(user_id=str(current_user.id)),
        session_id,
    )

    logger.info(f"Session deleted: {session_id}")
