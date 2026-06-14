"""Tests for RagConfig schemas and factory."""

from app.rag.factory import (
    build_chunking_strategy,
    build_extraction_strategy,
    build_retrieval_strategy,
)
from app.rag.retrieval.sparse import SparseRetrieval
from app.rag.chunking import FixedWindowChunking, RecursiveChunking
from app.schemas.rag_config import (
    ChunkingConfig,
    EffectiveRagConfig,
    ExtractionConfig,
    RagConfig,
    RetrievalConfig,
    extraction_fingerprint,
)


def test_extraction_fingerprint_stable() -> None:
    a = extraction_fingerprint(ExtractionConfig(strategy="ocr"))
    b = extraction_fingerprint(ExtractionConfig(strategy="ocr"))
    c = extraction_fingerprint(ExtractionConfig(strategy="vlm"))
    assert a == b
    assert a != c


def test_factory_builds_chunking_with_params() -> None:
    cfg = ChunkingConfig(
        strategy="fixed_window",
        params={"chunk_size": 256, "overlap": 20},
    )
    strategy = build_chunking_strategy(cfg)
    assert isinstance(strategy, FixedWindowChunking)
    assert strategy._chunk_size == 256


def test_factory_recursive() -> None:
    cfg = ChunkingConfig(strategy="recursive", params={"chunk_size": 400, "overlap": 40})
    assert isinstance(build_chunking_strategy(cfg), RecursiveChunking)


def test_rag_config_from_settings_shape() -> None:
    cfg = RagConfig.from_settings()
    assert cfg.extraction.strategy in ("ocr", "vlm")
    assert cfg.chunking.strategy in (
        "fixed_window",
        "recursive",
        "semantic",
        "parent_child",
    )


def test_effective_rag_config_top_k_from_request() -> None:
    project = RagConfig.from_settings()
    effective = EffectiveRagConfig.for_retrieval(project, None, top_k=3)
    assert effective.top_k == 3


def test_effective_rag_config_top_k_override_wins() -> None:
    from app.schemas.rag_config import RetrievalOverrides

    project = RagConfig.from_settings()
    effective = EffectiveRagConfig.for_retrieval(
        project,
        RetrievalOverrides(top_k=7),
        top_k=3,
    )
    assert effective.top_k == 7


def test_bm25_retrieval_factory() -> None:
    r = build_retrieval_strategy(
        RetrievalConfig(strategy="bm25", params={"k1": 1.2, "b": 0.8})
    )
    assert isinstance(r, SparseRetrieval)
    assert r.name == "bm25"
    assert r._k1 == 1.2
    assert r._b == 0.8


def test_hybrid_retrieval_factory() -> None:
    r = build_retrieval_strategy(RetrievalConfig(strategy="hybrid", params={"rrf_k": 42}))
    assert r.name == "hybrid"
    assert r._rrf_k == 42


def test_graph_local_retrieval_factory() -> None:
    from app.rag.retrieval.graph_local import GraphLocalRetrieval

    r = build_retrieval_strategy(
        RetrievalConfig(strategy="graph_local", params={"community_level": 3})
    )
    assert isinstance(r, GraphLocalRetrieval)
    assert r.name == "graph_local"
    assert r._community_level == 3
