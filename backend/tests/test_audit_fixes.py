"""Regression tests for critical audit fixes (graph_backend, wipe, in-flight)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.db.models import RagMode
from app.rag.chat.orchestrator import ChatOrchestrator
from app.rag.chat.stages.debug import StageTimer
from app.rag.pipeline import RAGPipeline
from app.rag.retrieval.base import RetrievalResult
from app.schemas.rag_config import (
    ChatConfig,
    ChatMultiQueryConfig,
    ChatMultihopConfig,
    GraphRagConfig,
)
from app.services.project_index_service import wipe_neo4j_graph


def test_wipe_neo4j_graph_calls_delete_project_subgraph() -> None:
    store = MagicMock()
    with patch(
        "app.services.project_index_service.get_neo4j_store", return_value=store
    ):
        wipe_neo4j_graph("proj-abc")
    store.delete_project_subgraph.assert_called_once_with("proj-abc")
    assert [c[0] for c in store.method_calls] == ["delete_project_subgraph"]


@pytest.mark.asyncio
async def test_pipeline_retrieve_passes_microsoft_graph_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RAGPipeline.retrieve must not drop graph_backend when building strategies."""
    cfg = GraphRagConfig.from_settings(graph_backend="microsoft")
    pipeline = RAGPipeline(cfg, rag_mode=RagMode.GRAPH)

    captured: list[object] = []

    class _FakeRetriever:
        name = "graph_local"

        async def retrieve(self, **kwargs):
            return []

    def _fake_build(config):
        captured.append(config)
        return _FakeRetriever()

    monkeypatch.setattr(
        "app.rag.pipeline.build_graph_retrieval_strategy", _fake_build
    )
    await pipeline.retrieve("what is X?", "proj-1", top_k=3)

    assert len(captured) == 1
    assert getattr(captured[0], "graph_backend") == "microsoft"


def test_celery_rebuild_passes_manage_in_flight_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Celery holds _in_flight; workspace must not re-acquire (would no-op)."""
    from app.services import celery_tasks as ct
    from app.services import graph_index_tasks as tasks

    pid = uuid4()
    tasks._in_flight.discard(str(pid))

    build_kwargs: dict = {}

    class _WS:
        async def build_index_for_project(self, project_id, **kwargs):
            build_kwargs.update(kwargs)
            build_kwargs["project_id"] = project_id

    def _run(coro):
        # Sync Celery path: drive the coroutine without asyncio.run (pytest loop).
        try:
            coro.send(None)
        except StopIteration as stop:
            return stop.value
        raise AssertionError("coroutine did not complete")

    monkeypatch.setattr(
        "app.services.graphrag_workspace.get_graphrag_workspace",
        lambda: _WS(),
    )
    monkeypatch.setattr(ct, "_run_async", _run)

    result = ct.rebuild_graph_index_task.run(str(pid))

    assert result["status"] == "ok"
    assert build_kwargs.get("manage_in_flight") is False
    assert build_kwargs.get("is_update") is True
    assert not tasks.is_graph_index_in_flight(pid)


@pytest.mark.asyncio
async def test_workspace_skips_when_in_flight_and_managing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import graph_index_tasks as tasks
    from app.services.graphrag_workspace import GraphRAGWorkspace

    pid = uuid4()
    tasks._in_flight.discard(str(pid))
    assert tasks._acquire_in_flight(pid) is True

    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graph_indexing_enabled", True
    )
    ws = GraphRAGWorkspace.__new__(GraphRAGWorkspace)
    # Should return immediately without touching DB
    await ws.build_index_for_project(pid, manage_in_flight=True)
    tasks._release_in_flight(pid)


@pytest.mark.asyncio
async def test_multihop_xor_multi_query_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both stages are enabled, multihop wins (elif multi_query)."""
    orch = object.__new__(ChatOrchestrator)
    orch.rag_mode = RagMode.VECTOR
    orch.llm = object()
    orch.chat_config = ChatConfig(
        multihop=ChatMultihopConfig(enabled=True, max_hops=2),
        multi_query=ChatMultiQueryConfig(enabled=True, count=3),
    )

    stage_calls: list[str] = []

    async def _analyze(llm, query, *, max_hops, graph_aware):
        stage_calls.append("multihop")
        return False, []

    async def _multi(llm, query, *, count):
        stage_calls.append("multi_query")
        return [query]

    async def _retrieve(query, *, top_k, overrides):
        return (
            [
                RetrievalResult(
                    content="hit",
                    score=0.9,
                    document_id="d1",
                    chunk_id="c1",
                    metadata={},
                )
            ],
            "dense",
            "none",
        )

    monkeypatch.setattr(
        "app.rag.chat.orchestrator.analyze_and_decompose", _analyze
    )
    monkeypatch.setattr(
        "app.rag.chat.orchestrator.generate_multi_queries", _multi
    )
    orch._pipeline_retrieve = _retrieve  # type: ignore[method-assign]
    orch._graph_aware_overrides = lambda o: o  # type: ignore[method-assign]

    timer = StageTimer()
    await orch._retrieve_staged(
        "complex question?", top_k=5, overrides=None, timer=timer
    )

    assert stage_calls == ["multihop"]
