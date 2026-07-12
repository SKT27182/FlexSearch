"""OpenSearch SearchStore hybrid + parent-child tests (mocked client)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.rag.retrieval.hybrid import HybridRetrieval
from app.rag.retrieval.parent_child import ParentChildRetrieval
from app.services.search_store.opensearch_store import OpenSearchStore
from app.services.search_store.types import SearchDocument, SearchFilters, SearchHit


def _hit(doc_id: str, score: float, **kwargs) -> SearchHit:
    return SearchHit(
        id=doc_id,
        score=score,
        content=kwargs.get("content", f"content-{doc_id}"),
        project_id=kwargs.get("project_id", "proj"),
        document_id=kwargs.get("document_id", "doc"),
        chunk_index=kwargs.get("chunk_index", 0),
        chunk_type=kwargs.get("chunk_type"),
        parent_id=kwargs.get("parent_id"),
        summary_level=kwargs.get("summary_level", "chunk"),
        filename=kwargs.get("filename", "f.txt"),
        payload=kwargs.get("payload", {}),
    )


class TestOpenSearchStoreHybrid:
    def test_rrf_fuses_dense_and_bm25(self) -> None:
        store = OpenSearchStore(url="http://localhost:9200", index_name="test_chunks")
        dense = [_hit("a", 0.9), _hit("b", 0.8), _hit("c", 0.7)]
        sparse = [_hit("b", 5.0), _hit("d", 4.0), _hit("a", 3.0)]
        fused = store._rrf([dense, sparse], k=60)
        assert fused[0].id == "b"  # appears in both lists near top
        assert "rrf_score" in fused[0].payload

    def test_hybrid_search_calls_both(self) -> None:
        store = OpenSearchStore(url="http://localhost:9200", index_name="test_chunks")
        with (
            patch.object(
                store,
                "dense_search",
                return_value=[_hit("a", 0.9), _hit("b", 0.5)],
            ) as dense,
            patch.object(
                store,
                "bm25_search",
                return_value=[_hit("b", 2.0), _hit("c", 1.0)],
            ) as bm25,
        ):
            results = store.hybrid_search(
                query="q",
                query_vector=[0.1] * 4,
                filters=SearchFilters(project_id="p", summary_level="chunk"),
                top_k=2,
            )
        dense.assert_called_once()
        bm25.assert_called_once()
        assert len(results) == 2
        assert results[0].id == "b"

    def test_index_mapping_includes_summary_level(self) -> None:
        store = OpenSearchStore(url="http://localhost:9200", index_name="test_chunks")
        body = store._index_body(384)
        props = body["mappings"]["properties"]
        assert props["summary_level"]["type"] == "keyword"
        assert props["embedding"]["type"] == "knn_vector"
        assert props["embedding"]["dimension"] == 384
        assert "member_chunk_ids" in props
        assert "cluster_id" in props


class TestParentChildRetrieval:
    @pytest.mark.asyncio
    async def test_scores_parent_by_best_child(self) -> None:
        store = MagicMock()
        store.dense_search.return_value = [
            _hit(
                "child-1",
                0.95,
                chunk_type="child",
                parent_id="parent-A",
                content="child match",
            ),
            _hit(
                "child-2",
                0.40,
                chunk_type="child",
                parent_id="parent-A",
                content="weaker child",
            ),
            _hit(
                "child-3",
                0.80,
                chunk_type="child",
                parent_id="parent-B",
                content="other",
            ),
        ]
        store.get_by_ids.return_value = [
            _hit(
                "parent-A",
                0.0,
                chunk_type="parent",
                content="PARENT A CONTEXT",
            ),
            _hit(
                "parent-B",
                0.0,
                chunk_type="parent",
                content="PARENT B CONTEXT",
            ),
        ]

        with (
            patch(
                "app.rag.retrieval.parent_child.get_search_store",
                return_value=store,
            ),
            patch(
                "app.rag.retrieval.parent_child.get_embedding_service",
            ) as emb,
        ):
            emb.return_value.embed.return_value = [0.1] * 8
            results = await ParentChildRetrieval().retrieve(
                "query", "proj", top_k=2
            )

        assert len(results) == 2
        assert results[0].chunk_id == "parent-A"
        assert results[0].score == 0.95
        assert results[0].content == "PARENT A CONTEXT"
        assert results[0].metadata["matched_child_id"] == "child-1"
        store.get_by_ids.assert_called_once()
        called_ids = store.get_by_ids.call_args[0][0]
        assert called_ids[0] == "parent-A"


class TestHybridRetrievalStrategy:
    @pytest.mark.asyncio
    async def test_hybrid_strategy_uses_search_store(self) -> None:
        store = MagicMock()
        store.hybrid_search.return_value = [
            _hit("x", 0.12, content="hybrid hit", payload={"rrf_score": 0.12})
        ]
        with (
            patch("app.rag.retrieval.hybrid.get_search_store", return_value=store),
            patch("app.rag.retrieval.hybrid.get_embedding_service") as emb,
        ):
            emb.return_value.embed.return_value = [0.2] * 4
            results = await HybridRetrieval().retrieve("q", "proj", top_k=3)

        assert len(results) == 1
        assert results[0].metadata["retrieval_type"] == "hybrid"
        store.hybrid_search.assert_called_once()


class TestSearchDocumentPayload:
    def test_default_summary_level_chunk(self) -> None:
        doc = SearchDocument(
            id="1",
            embedding=[0.1, 0.2],
            content="hello",
            project_id="p",
            document_id="d",
        )
        source = doc.to_source()
        assert source["summary_level"] == "chunk"
        assert "embedding" in source
