"""
FlexSearch Backend - Token Tracker Service

Track and persist LLM token usage.
"""

import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TokenUsage

logger = logging.getLogger(__name__)


class TokenTracker:
    """Token usage tracking service."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def track(
        self,
        user_id: UUID,
        project_id: UUID,
        session_id: str,
        model_name: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
    ) -> None:
        """
        Record token usage for an LLM call.

        Args:
            user_id: User who made the request
            project_id: Project context
            session_id: Chat session ID
            model_name: LLM model used
            provider: LLM provider
            input_tokens: Input/prompt tokens
            output_tokens: Output/completion tokens
            latency_ms: Request latency in milliseconds
        """
        usage = TokenUsage(
            user_id=user_id,
            project_id=project_id,
            session_id=session_id,
            model_name=model_name,
            provider=provider,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
        )

        self._db.add(usage)
        await self._db.commit()

        logger.debug(
            f"Tracked usage: model={model_name}, "
            f"input={input_tokens}, output={output_tokens}"
        )

    async def track_batch(
        self,
        records: list[dict],
    ) -> None:
        """
        Record multiple token usage records.

        Args:
            records: List of usage dictionaries
        """
        if not records:
            return

        await self._db.execute(
            insert(TokenUsage),
            records,
        )
        await self._db.commit()

        logger.debug(f"Tracked batch of {len(records)} usage records")


async def track_usage(
    db: AsyncSession,
    user_id: UUID,
    project_id: UUID,
    session_id: str,
    model_name: str,
    provider: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
) -> None:
    """
    Convenience function to track token usage.

    This is the primary interface for tracking - use this in API handlers.
    """
    tracker = TokenTracker(db)
    await tracker.track(
        user_id=user_id,
        project_id=project_id,
        session_id=session_id,
        model_name=model_name,
        provider=provider,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
    )
