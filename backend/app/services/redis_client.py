"""Async Redis client for document status pub/sub."""

from __future__ import annotations

import asyncio
import time
import redis.asyncio as aioredis

from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__)

_pool: aioredis.Redis | None = None
_available: bool | None = None
_next_retry_at = 0.0
_retry_delay = 1.0
_connect_lock: asyncio.Lock | None = None


async def get_redis() -> aioredis.Redis | None:
    global _pool, _available, _next_retry_at, _retry_delay, _connect_lock
    if _available is False and time.monotonic() < _next_retry_at:
        return None
    if _connect_lock is None:
        _connect_lock = asyncio.Lock()
    async with _connect_lock:
        if _pool is not None:
            return _pool
        try:
            _pool = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await _pool.ping()
            _available = True
            _retry_delay = 1.0
            _next_retry_at = 0.0
            logger.info("Redis connected for document events")
        except Exception as exc:
            logger.warning("Redis unavailable: %s", exc)
            _available = False
            _next_retry_at = time.monotonic() + _retry_delay
            _retry_delay = min(30.0, _retry_delay * 2)
            _pool = None
            return None
    return _pool


async def close_redis() -> None:
    global _pool, _available, _next_retry_at, _retry_delay, _connect_lock
    client = _pool
    # Detach first so a close failure cannot leave a client from a dead event
    # loop cached for the next Celery task.
    _pool = None
    _available = None
    _next_retry_at = 0.0
    _retry_delay = 1.0
    _connect_lock = None
    if client is not None:
        try:
            await client.aclose()
        except Exception as exc:
            logger.debug("Redis cleanup failed: %s", exc)
