"""Validate retrieval strategy against project rag_mode."""

from __future__ import annotations

from app.db.models import RagMode
from app.schemas.rag_config import (
    GraphRagConfig,
    RetrievalOverrides,
    VectorRagConfig,
)

VECTOR_STRATEGIES = frozenset({"dense", "bm25", "hybrid", "parent_child"})
GRAPH_STRATEGIES = frozenset({"graph_local", "graph_global"})


def effective_retrieval_strategy(
    rag_config: VectorRagConfig | GraphRagConfig,
    overrides: RetrievalOverrides | None,
) -> str:
    """Resolve strategy without building full effective config (avoids ValueError)."""
    if overrides and overrides.retrieval_strategy is not None:
        return overrides.retrieval_strategy
    return rag_config.retrieval.strategy


def validate_retrieval_for_mode(
    rag_mode: RagMode,
    rag_config: VectorRagConfig | GraphRagConfig,
    overrides: RetrievalOverrides | None,
) -> str | None:
    """Return error message if strategy mismatches mode, else None."""
    strategy = effective_retrieval_strategy(rag_config, overrides)
    mode = rag_mode.value if isinstance(rag_mode, RagMode) else str(rag_mode)
    if mode == "graph" and strategy not in GRAPH_STRATEGIES:
        return (
            f"Retrieval strategy '{strategy}' is not valid for graph projects. "
            "Use graph_local or graph_global."
        )
    if mode == "vector" and strategy not in VECTOR_STRATEGIES:
        return (
            f"Retrieval strategy '{strategy}' is not valid for vector projects. "
            "Use dense, bm25, hybrid, or parent_child."
        )
    return None
