"""SSE streaming for document processing status."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncGenerator
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus
from app.services.document_events import (
    document_channel,
    project_channel,
    status_payload_from_document,
)
from app.services.redis_client import get_redis

TERMINAL = {DocumentStatus.COMPLETED, DocumentStatus.FAILED}
POLL_INTERVAL_SEC = 2.0


def _format_sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def stream_document_events(
    db: AsyncSession,
    project_id: UUID,
    document_id: UUID,
) -> AsyncGenerator[str, None]:
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.project_id == project_id,
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        yield _format_sse("error", {"detail": "Document not found"})
        return

    yield _format_sse("snapshot", status_payload_from_document(document))
    if document.status in TERMINAL:
        yield _format_sse("close", {"reason": "terminal"})
        return

    redis = await get_redis()
    if redis is None:
        last_fp: tuple | None = None
        while True:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            result = await db.execute(
                select(Document).where(
                    Document.id == document_id,
                    Document.project_id == project_id,
                )
            )
            doc = result.scalar_one_or_none()
            if not doc:
                yield _format_sse("error", {"detail": "Document not found"})
                return
            fp = (doc.status, doc.progress_pct, doc.processing_step, doc.chunk_count)
            if fp != last_fp:
                last_fp = fp
                payload = status_payload_from_document(doc)
                yield _format_sse("status", payload)
            if doc.status in TERMINAL:
                yield _format_sse("close", {"reason": "terminal"})
                break
        return

    pubsub = redis.pubsub()
    channel = document_channel(document_id)
    await pubsub.subscribe(channel)
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=30.0,
            )
            if message is None:
                await asyncio.sleep(0.5)
                continue
            if message["type"] != "message":
                continue
            data = json.loads(message["data"])
            yield _format_sse("status", data)
            if data.get("status") in ("completed", "failed"):
                yield _format_sse("close", {"reason": "terminal"})
                break
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


async def stream_project_events(
    db: AsyncSession,
    project_id: UUID,
) -> AsyncGenerator[str, None]:
    result = await db.execute(
        select(Document)
        .where(Document.project_id == project_id)
        .where(Document.status.notin_(list(TERMINAL)))
    )
    active = result.scalars().all()
    snapshots = [status_payload_from_document(d) for d in active]
    yield _format_sse("snapshots", {"documents": snapshots})

    if not active:
        yield _format_sse("close", {"reason": "no_active"})
        return

    redis = await get_redis()
    if redis is None:
        last_seen: dict[str, tuple] = {}
        while True:
            await asyncio.sleep(POLL_INTERVAL_SEC)
            result = await db.execute(
                select(Document).where(Document.project_id == project_id)
            )
            docs = result.scalars().all()
            for doc in docs:
                key = str(doc.id)
                fp = (
                    doc.status,
                    doc.progress_pct,
                    doc.processing_step,
                    doc.chunk_count,
                )
                if last_seen.get(key) != fp:
                    last_seen[key] = fp
                    yield _format_sse("status", status_payload_from_document(doc))
            still_active = any(d.status not in TERMINAL for d in docs)
            if not still_active:
                yield _format_sse("close", {"reason": "all_terminal"})
                break
        return

    pubsub = redis.pubsub()
    channel = project_channel(project_id)
    await pubsub.subscribe(channel)
    try:
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=30.0,
            )
            if message is None:
                # Stop if no active documents left
                check = await db.execute(
                    select(Document.id)
                    .where(Document.project_id == project_id)
                    .where(Document.status.notin_(list(TERMINAL)))
                    .limit(1)
                )
                if check.scalar_one_or_none() is None:
                    yield _format_sse("close", {"reason": "all_terminal"})
                    break
                await asyncio.sleep(0.5)
                continue
            if message["type"] != "message":
                continue
            data = json.loads(message["data"])
            yield _format_sse("status", data)
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
