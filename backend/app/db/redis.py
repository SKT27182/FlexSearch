"""
FlexSearch Backend - Redis Connection

Redis client for session and conversation state.
"""

import logging
from typing import Any

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

# Redis connection pool
_redis_pool: redis.Redis | None = None


async def get_redis() -> redis.Redis:
    """Get Redis connection from pool."""
    global _redis_pool

    if _redis_pool is None:
        _redis_pool = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        logger.info("Redis connection pool created")

    return _redis_pool


async def close_redis() -> None:
    """Close Redis connection pool."""
    global _redis_pool

    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None
        logger.info("Redis connection pool closed")


class RedisSessionStore:
    """Redis-based session storage."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    async def set(
        self,
        key: str,
        value: str,
        expire_seconds: int | None = None,
    ) -> None:
        """Set a key-value pair."""
        if expire_seconds:
            await self._redis.setex(key, expire_seconds, value)
        else:
            await self._redis.set(key, value)

    async def get(self, key: str) -> str | None:
        """Get a value by key."""
        return await self._redis.get(key)

    async def delete(self, key: str) -> None:
        """Delete a key."""
        await self._redis.delete(key)

    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        return await self._redis.exists(key) > 0

    async def lpush(self, key: str, *values: Any) -> int:
        """Push values to a list (left)."""
        return await self._redis.lpush(key, *values)

    async def rpush(self, key: str, *values: Any) -> int:
        """Push values to a list (right)."""
        return await self._redis.rpush(key, *values)

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        """Get range of list elements."""
        return await self._redis.lrange(key, start, end)

    async def zadd(
        self,
        key: str,
        mapping: dict[str, float],
    ) -> int:
        """Add to sorted set."""
        return await self._redis.zadd(key, mapping)

    async def zrange(
        self,
        key: str,
        start: int,
        end: int,
        desc: bool = False,
    ) -> list[str]:
        """Get range from sorted set."""
        if desc:
            return await self._redis.zrevrange(key, start, end)
        return await self._redis.zrange(key, start, end)

    async def expire(self, key: str, seconds: int) -> bool:
        """Set key expiration."""
        return await self._redis.expire(key, seconds)
