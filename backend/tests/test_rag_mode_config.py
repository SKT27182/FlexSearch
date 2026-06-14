"""Tests for rag_mode configuration helpers."""

from app.db.models import RagMode
from app.schemas.rag_config import RagConfig
from app.services.retrieval_validation import validate_retrieval_for_mode


def test_for_mode_graph_defaults() -> None:
    cfg = RagConfig.for_mode(RagMode.GRAPH)
    assert cfg.retrieval.strategy == "graph_local"
    assert cfg.graph_indexing.enabled is True


def test_graph_indexing_fingerprint_changes_with_method() -> None:
    a = RagConfig.for_mode(RagMode.GRAPH)
    b = RagConfig.for_mode(RagMode.GRAPH)
    b.graph_indexing.method = "nlp"
    assert a.graph_indexing_fingerprint() != b.graph_indexing_fingerprint()


def test_validate_graph_strategy_on_vector_project() -> None:
    cfg = RagConfig.from_settings()
    err = validate_retrieval_for_mode(RagMode.VECTOR, cfg, None)
    assert err is None
    cfg.retrieval.strategy = "graph_local"
    err = validate_retrieval_for_mode(RagMode.VECTOR, cfg, None)
    assert err is not None


def test_validate_vector_strategy_on_graph_project() -> None:
    cfg = RagConfig.for_mode(RagMode.GRAPH)
    err = validate_retrieval_for_mode(RagMode.GRAPH, cfg, None)
    assert err is None
    cfg.retrieval.strategy = "dense"
    err = validate_retrieval_for_mode(RagMode.GRAPH, cfg, None)
    assert err is not None
