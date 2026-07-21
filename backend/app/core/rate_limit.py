"""
Simple sliding-window rate limiter for sensitive API routes.

Uses Redis when available; falls back to an in-process window so local/dev
and tests still enforce limits.
"""

from __future__ import annotations

import time
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.observability.metrics import metrics
from app.utils.logger import create_logger

logger = create_logger(__name__)


@dataclass(frozen=True)
class RateLimitRule:
    name: str
    limit: int
    window_seconds: int


# Named rules — values come from settings at call time
CHAT_RULE = "chat"
CRAWL_RULE = "crawl"
BULK_RULE = "bulk"
SENSITIVE_RULE = "sensitive"
LOGIN_RULE = "login"
REGISTER_RULE = "register"


def _rule_from_settings(name: str) -> RateLimitRule:
    if name == CHAT_RULE:
        return RateLimitRule(
            name=name,
            limit=settings.rate_limit_chat_per_minute,
            window_seconds=60,
        )
    if name == CRAWL_RULE:
        return RateLimitRule(
            name=name,
            limit=settings.rate_limit_crawl_per_minute,
            window_seconds=60,
        )
    if name == BULK_RULE:
        return RateLimitRule(
            name=name,
            limit=settings.rate_limit_bulk_per_minute,
            window_seconds=60,
        )
    if name == LOGIN_RULE:
        return RateLimitRule(
            name=name, limit=settings.rate_limit_login_per_minute, window_seconds=60
        )
    if name == REGISTER_RULE:
        return RateLimitRule(
            name=name, limit=settings.rate_limit_register_per_minute, window_seconds=60
        )
    return RateLimitRule(
        name=name,
        limit=settings.rate_limit_sensitive_per_minute,
        window_seconds=60,
    )


class _MemoryWindow:
    def __init__(self) -> None:
        self._hits: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            q = self._hits[key]
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


_memory = _MemoryWindow()


def client_key(request: Request, user_id: str | None = None) -> str:
    if user_id:
        return f"user:{user_id}"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return f"ip:{forwarded.split(',')[0].strip()}"
    if request.client:
        return f"ip:{request.client.host}"
    return "ip:unknown"


async def check_rate_limit(
    request: Request,
    rule_name: str,
    *,
    user_id: str | None = None,
) -> None:
    """Raise HTTP 429 when the caller exceeds the named rule."""
    if not settings.rate_limit_enabled:
        return

    rule = _rule_from_settings(rule_name)
    if rule.limit <= 0:
        return

    key = f"rl:{rule.name}:{client_key(request, user_id)}"
    allowed = await _allow_redis_or_memory(key, rule.limit, rule.window_seconds)
    if allowed:
        return

    metrics.rate_limit_hits.inc(rule=rule.name)
    logger.warning("Rate limit exceeded rule=%s key=%s", rule.name, key)
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            f"Rate limit exceeded for {rule.name} "
            f"({rule.limit} per {rule.window_seconds}s)"
        ),
        headers={"Retry-After": str(rule.window_seconds)},
    )


async def _allow_redis_or_memory(key: str, limit: int, window_seconds: int) -> bool:
    try:
        from app.services.redis_client import get_redis

        redis = await get_redis()
        if redis is not None:
            # Sliding window via sorted set of timestamps
            now = time.time()
            pipe = redis.pipeline()
            pipe.zremrangebyscore(key, 0, now - window_seconds)
            pipe.zadd(key, {str(now): now})
            pipe.zcard(key)
            pipe.expire(key, window_seconds + 1)
            results = await pipe.execute()
            count = int(results[2])
            return count <= limit
    except Exception as exc:
        logger.debug("Redis rate limit fallback to memory: %s", exc)

    return _memory.allow(key, limit, window_seconds)
