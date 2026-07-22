"""Regression tests for warnings and stale work observed in local run logs."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.rag.graph.extractor import GraphExtractor
from app.services.outbox import _dispatch_event


@pytest.mark.asyncio
async def test_stale_process_document_event_is_skipped() -> None:
    result = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    event = SimpleNamespace(
        event_type="process_document",
        aggregate_id=uuid4(),
        project_id=uuid4(),
        payload={"generation": 1},
    )

    await _dispatch_event(db, event)

    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_graph_extraction_allows_slow_large_models() -> None:
    llm = AsyncMock()
    llm.complete.return_value = SimpleNamespace(
        content='{"entities": [], "relationships": []}'
    )
    extractor = GraphExtractor()
    extractor._llm = llm

    await extractor.extract(str(uuid4()), "passage")

    assert llm.complete.await_args.kwargs["timeout_sec"] == 300.0
