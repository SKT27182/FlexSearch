"""Redis leases with token-safe release and renewal."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from typing import AsyncIterator

from app.services.redis_client import get_redis

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""


@asynccontextmanager
async def project_graph_lease(
    project_id: str, generation: int, *, ttl_ms: int = 90_000
) -> AsyncIterator[bool]:
    redis = await get_redis()
    if redis is None:
        raise RuntimeError("Redis is required for graph-build coordination")
    key = f"flexsearch:graph-build:{project_id}"
    token = f"{generation}:{secrets.token_urlsafe(24)}"
    acquired = bool(await redis.set(key, token, nx=True, px=ttl_ms))
    if not acquired:
        yield False
        return
    stopped = asyncio.Event()
    owner_task = asyncio.current_task()

    async def renew() -> None:
        while not stopped.is_set():
            try:
                await asyncio.wait_for(stopped.wait(), timeout=ttl_ms / 3000)
            except TimeoutError:
                try:
                    renewed = await redis.eval(_RENEW, 1, key, token, ttl_ms)
                except Exception:
                    if owner_task is not None:
                        owner_task.cancel("Graph-build lease renewal failed")
                    return
                if not renewed:
                    if owner_task is not None:
                        owner_task.cancel("Graph-build lease was lost")
                    return

    renew_task = asyncio.create_task(renew())
    try:
        yield True
    finally:
        stopped.set()
        renew_task.cancel()
        await asyncio.gather(renew_task, return_exceptions=True)
        await redis.eval(_RELEASE, 1, key, token)
