"""Async Redis client for document status pub/sub."""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__)

_pool: aioredis.Redis | None = None
_available: bool | None = None


async def get_redis() -> aioredis.Redis | None:
    global _pool, _available
    if _available is False:
        return None
    if _pool is None:
        try:
            _pool = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await _pool.ping()
            _available = True
            logger.info("Redis connected for document events")
        except Exception as exc:
            logger.warning("Redis unavailable: %s", exc)
            _available = False
            _pool = None
            return None
    return _pool


async def close_redis() -> None:
    global _pool, _available
    if _pool is not None:
        await _pool.aclose()
        _pool = None
    _available = None
