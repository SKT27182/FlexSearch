"""Regression tests for critical audit fixes (graph_backend, wipe, in-flight)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

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

    monkeypatch.setattr("app.rag.pipeline.build_graph_retrieval_strategy", _fake_build)
    await pipeline.retrieve("what is X?", "proj-1", top_k=3)

    assert len(captured) == 1
    assert getattr(captured[0], "graph_backend") == "microsoft"


@pytest.mark.asyncio
async def test_distributed_graph_lease_coalesces_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.distributed_lock import project_graph_lease

    class FakeRedis:
        value = None

        async def set(self, _key, value, *, nx, px):
            if nx and self.value is not None:
                return False
            self.value = value
            return True

        async def eval(self, script, _count, _key, token, *args):
            if self.value != token:
                return 0
            if "del" in script:
                self.value = None
            return 1

    redis = FakeRedis()

    async def get_fake_redis():
        return redis

    monkeypatch.setattr("app.services.distributed_lock.get_redis", get_fake_redis)
    async with project_graph_lease("project", 4, ttl_ms=60_000) as acquired:
        assert acquired is True
        async with project_graph_lease("project", 4, ttl_ms=60_000) as duplicate:
            assert duplicate is False
    async with project_graph_lease("project", 4, ttl_ms=60_000) as reacquired:
        assert reacquired is True


@pytest.mark.asyncio
async def test_workspace_skips_when_in_flight_and_managing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.distributed_lock import project_graph_lease

    async def no_redis():
        return None

    monkeypatch.setattr("app.services.distributed_lock.get_redis", no_redis)
    with pytest.raises(RuntimeError, match="Redis is required"):
        async with project_graph_lease("project", 1):
            pass


@pytest.mark.asyncio
async def test_distributed_graph_lease_cancels_owner_when_renewal_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.distributed_lock import project_graph_lease

    class LostLeaseRedis:
        async def set(self, *_args, **_kwargs):
            return True

        async def eval(self, script, *_args):
            return 1 if "del" in script else 0

    async def get_fake_redis():
        return LostLeaseRedis()

    monkeypatch.setattr("app.services.distributed_lock.get_redis", get_fake_redis)
    with pytest.raises(asyncio.CancelledError, match="lease was lost"):
        async with project_graph_lease("project", 1, ttl_ms=30) as acquired:
            assert acquired is True
            await asyncio.sleep(0.1)


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

    monkeypatch.setattr("app.rag.chat.orchestrator.analyze_and_decompose", _analyze)
    monkeypatch.setattr("app.rag.chat.orchestrator.generate_multi_queries", _multi)
    orch._pipeline_retrieve = _retrieve  # type: ignore[method-assign]
    orch._graph_aware_overrides = lambda o: o  # type: ignore[method-assign]

    timer = StageTimer()
    await orch._retrieve_staged(
        "complex question?", top_k=5, overrides=None, timer=timer
    )

    assert stage_calls == ["multihop"]
