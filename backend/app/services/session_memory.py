"""Redis short-term session memory for chat rewrite / conversational context.

Primary store is Redis. When Redis misses (TTL expiry, cold start), the chat
orchestrator hydrates from Postgres ``chat_turns`` via ChatHistoryService and
re-warms Redis — see ChatOrchestrator._load_history.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from app.services.redis_client import get_redis
from app.utils.logger import create_logger

logger = create_logger(__name__)

KEY_PREFIX = "flexsearch:chat:memory:"


def memory_key(session_id: UUID | str) -> str:
    return f"{KEY_PREFIX}{session_id}"


class SessionMemoryService:
    """
    Short-term conversational memory in Redis.

    Used by rewrite / clarify stages. On miss, callers should hydrate from
    Postgres (orchestrator does this automatically).
    """

    async def get_turns(
        self,
        session_id: UUID | str,
        *,
        max_turns: int = 10,
    ) -> list[dict[str, Any]]:
        try:
            redis = await get_redis()
            if redis is None:
                return []
            raw = await redis.get(memory_key(session_id))
        except Exception as exc:
            logger.debug("Session memory get failed: %s", exc)
            return []
        if not raw:
            return []
        try:
            turns = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Corrupt session memory for %s", session_id)
            return []
        if not isinstance(turns, list):
            return []
        return turns[-max_turns:]

    async def append_turn(
        self,
        session_id: UUID | str,
        *,
        role: str,
        content: str,
        ttl_seconds: int = 3600,
        max_turns: int = 20,
    ) -> None:
        try:
            redis = await get_redis()
            if redis is None:
                logger.debug("Redis unavailable; skipping session memory append")
                return
            key = memory_key(session_id)
            turns = await self.get_turns(session_id, max_turns=max_turns * 2)
            turns.append({"role": role, "content": content})
            turns = turns[-(max_turns * 2) :]
            await redis.set(key, json.dumps(turns), ex=ttl_seconds)
        except Exception as exc:
            logger.debug("Session memory append failed: %s", exc)

    async def clear(self, session_id: UUID | str) -> None:
        try:
            redis = await get_redis()
            if redis is None:
                return
            await redis.delete(memory_key(session_id))
        except Exception as exc:
            logger.debug("Session memory clear failed: %s", exc)

    async def replace_turns(
        self,
        session_id: UUID | str,
        turns: list[dict[str, Any]],
        *,
        ttl_seconds: int = 3600,
    ) -> None:
        """Replace memory (used after Postgres hydrate)."""
        try:
            redis = await get_redis()
            if redis is None:
                return
            await redis.set(
                memory_key(session_id),
                json.dumps(turns),
                ex=ttl_seconds,
            )
        except Exception as exc:
            logger.debug("Session memory replace failed: %s", exc)
