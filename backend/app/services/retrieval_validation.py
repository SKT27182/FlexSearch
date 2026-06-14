"""Validate retrieval strategy against project rag_mode."""

from __future__ import annotations

from app.db.models import RagMode
from app.schemas.rag_config import EffectiveRagConfig, RagConfig, RetrievalOverrides

VECTOR_STRATEGIES = frozenset({"dense", "bm25", "hybrid", "parent_child"})
GRAPH_STRATEGIES = frozenset({"graph_local", "graph_global"})


def effective_retrieval_strategy(
    rag_config: RagConfig,
    overrides: RetrievalOverrides | None,
) -> str:
    effective = EffectiveRagConfig.for_retrieval(rag_config, overrides)
    return effective.retrieval.strategy


def validate_retrieval_for_mode(
    rag_mode: RagMode,
    rag_config: RagConfig,
    overrides: RetrievalOverrides | None,
) -> str | None:
    """Return error message if strategy mismatches mode, else None."""
    strategy = effective_retrieval_strategy(rag_config, overrides)
    mode = rag_mode.value if isinstance(rag_mode, RagMode) else str(rag_mode)
    if mode == "graph" and strategy in VECTOR_STRATEGIES:
        return (
            f"Retrieval strategy '{strategy}' is not valid for graph projects. "
            "Use graph_local or graph_global."
        )
    if mode == "vector" and strategy in GRAPH_STRATEGIES:
        return (
            f"Retrieval strategy '{strategy}' is not valid for vector projects. "
            "Use dense, bm25, hybrid, or parent_child."
        )
    return None
