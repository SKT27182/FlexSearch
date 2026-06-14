"""Tests for GraphRAG context mapping."""

import pandas as pd

from app.rag.retrieval.graph_context_mapper import context_to_retrieval_results


def test_context_dict_of_lists() -> None:
    context = {
        "text_units": [
            {"id": "tu-1", "text": "Hello world", "document_id": "doc-1", "score": 0.9},
            {"id": "tu-2", "text": "Second unit", "document_id": "doc-2"},
        ]
    }
    results = context_to_retrieval_results(context, top_k=5)
    assert len(results) == 2
    assert results[0].content == "Hello world"
    assert results[0].document_id == "doc-1"
    assert results[0].chunk_id == "tu-1"


def test_context_dataframe() -> None:
    df = pd.DataFrame([{"id": "e1", "description": "Entity one", "score": 0.8}])
    results = context_to_retrieval_results(df, top_k=3)
    assert len(results) == 1
    assert "Entity one" in results[0].content


def test_empty_context() -> None:
    assert context_to_retrieval_results(None, top_k=5) == []
