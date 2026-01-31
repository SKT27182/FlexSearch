"""
FlexSearch Backend - No Reranking Strategy

Pass-through strategy (fast mode).
"""

from app.rag.reranking.base import BaseRerankingStrategy
from app.rag.retrieval.base import RetrievalResult


class NoReranking(BaseRerankingStrategy):
    """No reranking - pass through results as-is."""

    @property
    def name(self) -> str:
        return "none"

    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Return results unchanged, optionally limited."""
        if top_k is not None:
            return results[:top_k]
        return results
