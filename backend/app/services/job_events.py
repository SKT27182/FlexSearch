"""Redis pub/sub for long-running job progress (crawl / bulk)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import redis as sync_redis

from app.core.config import settings
from app.services.redis_client import get_redis
from app.utils.logger import create_logger

logger = create_logger(__name__)

JOB_CHANNEL = "flexsearch:job:{job_id}"
JOB_LAST_KEY = "flexsearch:job:{job_id}:last"
JOB_META_KEY = "flexsearch:job:{job_id}:meta"
JOB_TTL_SEC = 60 * 60 * 6

# In-process fallback when Redis is unavailable (tests / degraded mode)
_META_FALLBACK: dict[str, dict[str, Any]] = {}


def job_channel(job_id: str) -> str:
    return JOB_CHANNEL.format(job_id=job_id)


def job_last_key(job_id: str) -> str:
    return JOB_LAST_KEY.format(job_id=job_id)


def job_meta_key(job_id: str) -> str:
    return JOB_META_KEY.format(job_id=job_id)


def parse_project_id_from_job_id(job_id: str) -> str | None:
    """Extract project_id from crawl job ids shaped ``crawl:{uuid}:{hex}``."""
    if not job_id.startswith("crawl:"):
        return None
    parts = job_id.split(":")
    if len(parts) < 3:
        return None
    candidate = parts[1]
    try:
        UUID(candidate)
        return candidate
    except ValueError:
        return None


def register_job_meta_sync(
    job_id: str,
    *,
    project_id: str | UUID,
    job_type: str = "job",
    owner_user_id: str | UUID | None = None,
) -> None:
    """Store job→project ACL metadata (Celery / sync callers)."""
    payload = {
        "job_id": job_id,
        "project_id": str(project_id),
        "job_type": job_type,
        "owner_user_id": str(owner_user_id) if owner_user_id else None,
    }
    _META_FALLBACK[job_id] = payload
    message = json.dumps(payload, default=str)
    try:
        client = sync_redis.from_url(
            settings.redis_url or "",
            encoding="utf-8",
            decode_responses=True,
        )
        try:
            client.setex(job_meta_key(job_id), JOB_TTL_SEC, message)
        finally:
            client.close()
    except Exception as exc:
        logger.warning("Failed to register job meta (sync): %s", exc)


async def register_job_meta(
    job_id: str,
    *,
    project_id: str | UUID,
    job_type: str = "job",
    owner_user_id: str | UUID | None = None,
) -> None:
    """Store job→project ACL metadata (async API)."""
    payload = {
        "job_id": job_id,
        "project_id": str(project_id),
        "job_type": job_type,
        "owner_user_id": str(owner_user_id) if owner_user_id else None,
    }
    _META_FALLBACK[job_id] = payload
    client = await get_redis()
    if client is None:
        return
    try:
        await client.setex(
            job_meta_key(job_id), JOB_TTL_SEC, json.dumps(payload, default=str)
        )
    except Exception as exc:
        logger.warning("Failed to register job meta: %s", exc)


async def get_job_meta(job_id: str) -> dict[str, Any] | None:
    """Resolve job metadata for ACL (Redis → fallback → crawl id parse)."""
    client = await get_redis()
    if client is not None:
        try:
            raw = await client.get(job_meta_key(job_id))
            if raw:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("Failed to read job meta: %s", exc)

    if job_id in _META_FALLBACK:
        return _META_FALLBACK[job_id]

    parsed = parse_project_id_from_job_id(job_id)
    if parsed:
        return {"job_id": job_id, "project_id": parsed, "job_type": "crawl"}

    # Last resort: project_id on last event payload
    last = await get_last_job_event(job_id)
    if last and last.get("project_id"):
        return {
            "job_id": job_id,
            "project_id": str(last["project_id"]),
            "job_type": last.get("job_type") or "job",
        }
    return None


def publish_job_event_sync(job_id: str, payload: dict[str, Any]) -> None:
    """Publish from Celery workers (sync Redis)."""
    message = json.dumps(payload, default=str)
    try:
        client = sync_redis.from_url(
            settings.redis_url or "",
            encoding="utf-8",
            decode_responses=True,
        )
        try:
            client.publish(job_channel(job_id), message)
            client.setex(job_last_key(job_id), JOB_TTL_SEC, message)
        finally:
            client.close()
    except Exception as exc:
        logger.warning("Failed to publish job event (sync): %s", exc)


async def publish_job_event(job_id: str, payload: dict[str, Any]) -> None:
    """Publish from async API / workers."""
    client = await get_redis()
    if client is None:
        return
    message = json.dumps(payload, default=str)
    try:
        await client.publish(job_channel(job_id), message)
        await client.setex(job_last_key(job_id), JOB_TTL_SEC, message)
    except Exception as exc:
        logger.warning("Failed to publish job event: %s", exc)


async def get_last_job_event(job_id: str) -> dict[str, Any] | None:
    client = await get_redis()
    if client is None:
        return None
    try:
        raw = await client.get(job_last_key(job_id))
        if not raw:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Failed to read last job event: %s", exc)
        return None
