"""Document SSE must recover status transitions missed by Redis pub/sub."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.document_sse import stream_document_events, stream_project_events
from app.db.models import DocumentStatus


def _document(status: DocumentStatus) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        status=status,
        processing_step="Graph indexed" if status == DocumentStatus.COMPLETED else "Indexing graph",
        progress_pct=100 if status == DocumentStatus.COMPLETED else 75,
        chunk_count=2 if status == DocumentStatus.COMPLETED else 0,
        error_message=None,
        filename="graph.pdf",
    )


def _sse_data(chunk: str) -> dict:
    data_line = next(line for line in chunk.splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


@pytest.mark.asyncio
async def test_document_stream_reconciles_missed_terminal_event(monkeypatch) -> None:
    indexing = _document(DocumentStatus.GRAPH_INDEXING)
    completed = _document(DocumentStatus.COMPLETED)
    completed.id = indexing.id
    completed.project_id = indexing.project_id

    initial = MagicMock()
    initial.scalar_one_or_none.return_value = indexing
    reconciled = MagicMock()
    reconciled.scalar_one_or_none.return_value = completed
    db = SimpleNamespace(execute=AsyncMock(side_effect=[initial, reconciled]))

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.get_message = AsyncMock(return_value=None)
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    redis = MagicMock()
    redis.pubsub.return_value = pubsub
    monkeypatch.setattr(
        "app.api.document_sse.get_redis", AsyncMock(return_value=redis)
    )

    stream = stream_document_events(db, indexing.project_id, indexing.id)
    assert "event: snapshot" in await anext(stream)
    status = await anext(stream)
    assert "event: status" in status
    assert _sse_data(status)["status"] == "completed"
    assert "event: close" in await anext(stream)
    await stream.aclose()


@pytest.mark.asyncio
async def test_project_stream_reconciles_missed_terminal_event(monkeypatch) -> None:
    indexing = _document(DocumentStatus.GRAPH_INDEXING)
    completed = _document(DocumentStatus.COMPLETED)
    completed.id = indexing.id
    completed.project_id = indexing.project_id

    initial = MagicMock()
    initial.scalars.return_value.all.return_value = [indexing]
    reconciled = MagicMock()
    reconciled.scalars.return_value.all.return_value = [completed]
    db = SimpleNamespace(execute=AsyncMock(side_effect=[initial, reconciled]))

    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.get_message = AsyncMock(return_value=None)
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()
    redis = MagicMock()
    redis.pubsub.return_value = pubsub
    monkeypatch.setattr(
        "app.api.document_sse.get_redis", AsyncMock(return_value=redis)
    )

    stream = stream_project_events(db, indexing.project_id)
    assert "event: snapshots" in await anext(stream)
    status = await anext(stream)
    assert "event: status" in status
    assert _sse_data(status)["status"] == "completed"
    assert "event: close" in await anext(stream)
    await stream.aclose()
