"""Tests for BM25-only (lexical) retrieval."""

from unittest.mock import AsyncMock, patch

import pytest

from app.rag.retrieval.bm25_index import BM25
from app.rag.retrieval.sparse import SparseRetrieval


@pytest.mark.asyncio
async def test_sparse_retrieval_returns_bm25_results() -> None:
    index = BM25()
    index.fit(
        ["alpha beta document", "gamma delta text"],
        ["id-1", "id-2"],
        [
            {
                "content": "alpha beta document",
                "document_id": "doc-1",
                "filename": "a.txt",
                "chunk_index": 0,
            },
            {
                "content": "gamma delta text",
                "document_id": "doc-2",
                "filename": "b.txt",
                "chunk_index": 1,
            },
        ],
    )

    retriever = SparseRetrieval()
    retriever._bm25 = index
    retriever._bm25_project_id = "proj-1"

    results = await retriever.retrieve("alpha", "proj-1", top_k=2)

    assert retriever.name == "bm25"
    assert len(results) >= 1
    assert results[0].metadata["retrieval_type"] == "bm25"
    assert "alpha" in results[0].content.lower()


@pytest.mark.asyncio
async def test_sparse_retrieval_builds_index_on_first_call() -> None:
    retriever = SparseRetrieval()

    with patch(
        "app.rag.retrieval.sparse.build_project_bm25_index",
        new_callable=AsyncMock,
        return_value=None,
    ) as mock_build:
        results = await retriever.retrieve("query", "new-project", top_k=3)

    mock_build.assert_awaited_once_with("new-project", k1=1.5, b=0.75)
    assert results == []
