"""Tests for OpenSearch-backed BM25 (sparse) retrieval."""

from unittest.mock import MagicMock, patch

import pytest

from app.rag.retrieval.sparse import SparseRetrieval
from app.services.search_store.types import SearchHit


@pytest.mark.asyncio
async def test_sparse_retrieval_returns_bm25_results() -> None:
    store = MagicMock()
    store.bm25_search.return_value = [
        SearchHit(
            id="id-1",
            score=2.5,
            content="alpha beta document",
            document_id="doc-1",
            filename="a.txt",
            chunk_index=0,
            summary_level="chunk",
        )
    ]

    with patch("app.rag.retrieval.sparse.get_search_store", return_value=store):
        retriever = SparseRetrieval()
        results = await retriever.retrieve("alpha", "proj-1", top_k=2)

    assert retriever.name == "bm25"
    assert len(results) == 1
    assert results[0].metadata["retrieval_type"] == "bm25"
    assert "alpha" in results[0].content.lower()
    store.bm25_search.assert_called_once()
    # OpenSearch BM25 does not take k1/b from SparseRetrieval.
    _, kwargs = store.bm25_search.call_args
    assert "k1" not in kwargs
    assert "b" not in kwargs
