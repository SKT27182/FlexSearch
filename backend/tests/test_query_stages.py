"""Phase 2 query-stage unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.rag.chat.stages.context_expand import expand_neighbors
from app.rag.chat.stages.fusion import frequency_consensus_fuse
from app.rag.chat.stages.multi_query import _parse_query_list
from app.rag.chat.stages.multihop import _parse_hops
from app.rag.retrieval.base import RetrievalResult
from app.services.search_store.opensearch_store import OpenSearchStore
from app.services.search_store.types import SearchFilters, SearchHit


def _result(
    chunk_id: str,
    score: float,
    *,
    document_id: str = "doc-1",
    chunk_index: int = 0,
    content: str | None = None,
    summary_level: str = "chunk",
) -> RetrievalResult:
    return RetrievalResult(
        content=content or f"content-{chunk_id}",
        score=score,
        document_id=document_id,
        chunk_id=chunk_id,
        metadata={
            "chunk_index": chunk_index,
            "filename": "a.txt",
            "summary_level": summary_level,
        },
    )


class TestFrequencyConsensus:
    def test_boosts_overlapping_chunks(self) -> None:
        a = [_result("c1", 0.9), _result("c2", 0.8)]
        b = [_result("c2", 0.7), _result("c3", 0.6)]
        fused = frequency_consensus_fuse([a, b], top_k=3, frequency_boost=0.15)
        assert fused[0].chunk_id == "c2"
        assert fused[0].metadata["consensus_count"] == 2
        assert fused[0].score == pytest.approx(0.8 + 0.15)

    def test_single_list_passthrough(self) -> None:
        a = [_result("c1", 0.9), _result("c2", 0.5)]
        fused = frequency_consensus_fuse([a], top_k=1)
        assert len(fused) == 1
        assert fused[0].chunk_id == "c1"


class TestMultiQueryParse:
    def test_parses_json_array(self) -> None:
        raw = '["What is X?", "Explain X", "X overview"]'
        out = _parse_query_list(raw, count=3, original="What is X?")
        assert out[0] == "What is X?"
        assert len(out) == 3

    def test_parses_bullets(self) -> None:
        raw = "- alpha\n- beta\n- gamma"
        out = _parse_query_list(raw, count=3, original="q")
        assert "q" in out
        assert len(out) == 3


class TestMultihopParse:
    def test_parses_json_needed(self) -> None:
        raw = '{"multihop": true, "hops": ["Who founded X?", "Where is X based?"]}'
        needed, hops = _parse_hops(raw, max_hops=2, original="Tell me about X")
        assert needed is True
        assert len(hops) == 2

    def test_not_needed(self) -> None:
        raw = '{"multihop": false, "hops": []}'
        needed, hops = _parse_hops(raw, max_hops=2, original="What is X?")
        assert needed is False
        assert hops == ["What is X?"]


class TestSearchFiltersChunkIndexRange:
    def test_filter_clause_includes_range(self) -> None:
        store = OpenSearchStore(url="http://localhost:9200", index_name="t")
        clauses = store._filter_clause(
            SearchFilters(
                project_id="p",
                document_id="d",
                chunk_index_min=1,
                chunk_index_max=3,
            )
        )
        assert {"term": {"project_id": "p"}} in clauses
        assert {"term": {"document_id": "d"}} in clauses
        assert {"range": {"chunk_index": {"gte": 1, "lte": 3}}} in clauses


class TestContextExpand:
    @pytest.mark.asyncio
    async def test_expands_neighbors_by_chunk_index(self) -> None:
        primary = _result("c1", 0.9, chunk_index=2, content="middle")
        store = MagicMock()
        store.scroll.return_value = (
            [
                SearchHit(
                    id="c0",
                    score=0.0,
                    content="before",
                    project_id="p",
                    document_id="doc-1",
                    chunk_index=1,
                    filename="a.txt",
                ),
                SearchHit(
                    id="c1",
                    score=0.0,
                    content="middle",
                    project_id="p",
                    document_id="doc-1",
                    chunk_index=2,
                    filename="a.txt",
                ),
                SearchHit(
                    id="c2",
                    score=0.0,
                    content="after",
                    project_id="p",
                    document_id="doc-1",
                    chunk_index=3,
                    filename="a.txt",
                ),
            ],
            None,
        )
        expanded = await expand_neighbors(
            [primary],
            project_id="p",
            context_window=1,
            store=store,
        )
        ids = [r.chunk_id for r in expanded]
        assert ids == ["c0", "c1", "c2"]
        store.scroll.assert_called_once()
        filters = store.scroll.call_args[0][0]
        assert filters.chunk_index_min == 1
        assert filters.chunk_index_max == 3

    @pytest.mark.asyncio
    async def test_noop_when_window_zero(self) -> None:
        primary = _result("c1", 0.9)
        out = await expand_neighbors([primary], project_id="p", context_window=0)
        assert out == [primary]

    @pytest.mark.asyncio
    async def test_skips_summary_level_hits(self) -> None:
        """Cluster/document hits must not expand via chunk_index neighbors."""
        primary = _result(
            "sum-1",
            0.95,
            chunk_index=0,
            summary_level="cluster",
            content="cluster summary",
        )
        store = MagicMock()
        expanded = await expand_neighbors(
            [primary],
            project_id="p",
            context_window=2,
            store=store,
        )
        assert [r.chunk_id for r in expanded] == ["sum-1"]
        store.scroll.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_document_summary_hits(self) -> None:
        primary = _result(
            "doc-sum",
            0.8,
            chunk_index=5,
            summary_level="document",
        )
        store = MagicMock()
        expanded = await expand_neighbors(
            [primary],
            project_id="p",
            context_window=1,
            store=store,
        )
        assert len(expanded) == 1
        store.scroll.assert_not_called()
