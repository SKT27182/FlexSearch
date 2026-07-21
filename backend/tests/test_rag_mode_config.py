"""Graph mode config defaults."""

from app.schemas.rag_config import GraphRagConfig, VectorRagConfig


def test_graph_mode_defaults_microsoft() -> None:
    cfg = GraphRagConfig.from_settings(graph_backend="microsoft")
    assert cfg.graph_backend == "microsoft"
    assert cfg.microsoft_indexing.enabled is True


def test_graph_indexing_fingerprint_changes_with_method() -> None:
    a = GraphRagConfig.from_settings(graph_backend="microsoft")
    b = GraphRagConfig.from_settings(graph_backend="microsoft")
    b.microsoft_indexing.method = "nlp"
    assert a.graph_indexing_fingerprint() != b.graph_indexing_fingerprint()


def test_vector_mode_has_chunking() -> None:
    cfg = VectorRagConfig.from_db(
        {"chunking": {"strategy": "fixed_window", "params": {}}}
    )
    assert cfg.chunking.strategy == "fixed_window"


def test_graph_neo4j_defaults() -> None:
    cfg = GraphRagConfig.from_settings(graph_backend="neo4j")
    assert cfg.graph_backend == "neo4j"
    assert cfg.indexing.embed_entities is True
