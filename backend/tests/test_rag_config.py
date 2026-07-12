"""Tests for RagConfig schemas and factory."""

from app.db.models import RagMode
from app.rag.factory import (
    build_chunking_strategy,
    build_extraction_strategy,
    build_graph_retrieval_strategy,
    build_retrieval_strategy,
)
from app.rag.retrieval.graph_local import GraphLocalRetrieval
from app.rag.retrieval.sparse import SparseRetrieval
from app.rag.chunking import FixedWindowChunking, RecursiveChunking
from app.schemas.rag_config import (
    ChunkingConfig,
    EffectiveRagConfig,
    ExtractionConfig,
    GraphRagConfig,
    GraphRetrievalConfig,
    VectorRagConfig,
    RetrievalConfig,
    VECTOR_RETRIEVAL_STRATEGIES,
    GRAPH_RETRIEVAL_STRATEGIES,
    extraction_fingerprint,
    parse_rag_config,
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
    cfg = VectorRagConfig.from_settings()
    assert cfg.extraction.strategy in ("ocr", "vlm", "docling", "hybrid_pdf")
    assert cfg.summaries.retrieval_mode == "chunks_only"
    assert cfg.chunking.strategy in (
        "fixed_window",
        "recursive",
        "semantic",
        "parent_child",
    )


def test_effective_rag_config_top_k_from_request() -> None:
    project = VectorRagConfig.from_settings()
    effective = EffectiveRagConfig.for_retrieval(project, None, top_k=3)
    assert effective.top_k == 3


def test_effective_rag_config_top_k_override_wins() -> None:
    from app.schemas.rag_config import RetrievalOverrides

    project = VectorRagConfig.from_settings()
    effective = EffectiveRagConfig.for_retrieval(
        project,
        RetrievalOverrides(top_k=7),
        top_k=3,
    )
    assert effective.top_k == 7


def test_bm25_retrieval_factory() -> None:
    """Factory stores k1/b for schema compat; OpenSearch ignores them at search time."""
    r = build_retrieval_strategy(
        RetrievalConfig(strategy="bm25", params={"k1": 1.2, "b": 0.8})
    )
    assert isinstance(r, SparseRetrieval)
    assert r.name == "bm25"
    # Retained on the instance for config round-trip only (not passed to OpenSearch).
    assert r._k1 == 1.2
    assert r._b == 0.8


def test_hybrid_retrieval_factory() -> None:
    r = build_retrieval_strategy(RetrievalConfig(strategy="hybrid", params={"rrf_k": 42}))
    assert r.name == "hybrid"
    assert r._rrf_k == 42


def test_microsoft_graph_local_retrieval_factory() -> None:
    from app.rag.retrieval.graph_local import GraphLocalRetrieval

    cfg = GraphRagConfig.from_settings(graph_backend="microsoft")
    r = build_graph_retrieval_strategy(cfg)
    assert isinstance(r, GraphLocalRetrieval)
    assert r.name == "graph_local"
    assert r._graph_backend == "microsoft"


def test_graph_effective_config_preserves_microsoft_backend() -> None:
    """GraphEffectiveRagConfig must carry graph_backend into the factory."""
    from app.schemas.rag_config import GraphEffectiveRagConfig

    project = GraphRagConfig.from_settings(graph_backend="microsoft")
    effective = GraphEffectiveRagConfig.for_retrieval(project, None, top_k=5)
    assert effective.graph_backend == "microsoft"
    r = build_graph_retrieval_strategy(effective)
    assert r._graph_backend == "microsoft"


def test_bare_graph_retrieval_config_defaults_to_neo4j() -> None:
    """Passing only GraphRetrievalConfig still defaults to neo4j (legacy path)."""
    r = build_graph_retrieval_strategy(
        GraphRetrievalConfig(strategy="graph_local", params={"max_hops": 2})
    )
    assert isinstance(r, GraphLocalRetrieval)
    assert r._graph_backend == "neo4j"


def test_neo4j_graph_retrieval_factory() -> None:
    r = build_graph_retrieval_strategy(
        GraphRagConfig(
            graph_backend="neo4j",
            retrieval=GraphRetrievalConfig(
                strategy="graph_local", params={"max_hops": 2}
            ),
        )
    )
    assert isinstance(r, GraphLocalRetrieval)
    assert r.name == "graph_local"
    assert r._graph_backend == "neo4j"


def test_parse_rag_config_modes() -> None:
    vector = parse_rag_config(RagMode.VECTOR, {"chunking": {"strategy": "fixed_window"}})
    graph = parse_rag_config(RagMode.GRAPH, {"retrieval": {"strategy": "graph_global"}})
    assert isinstance(vector, VectorRagConfig)
    assert isinstance(graph, GraphRagConfig)


def test_retrieval_strategy_sets() -> None:
    assert "dense" in VECTOR_RETRIEVAL_STRATEGIES
    assert "graph_local" in GRAPH_RETRIEVAL_STRATEGIES
