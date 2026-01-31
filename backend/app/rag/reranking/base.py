"""
FlexSearch Backend - Base Reranking Strategy

Abstract base class for reranking strategies.
"""

from abc import ABC, abstractmethod

from app.rag.retrieval.base import RetrievalResult


class BaseRerankingStrategy(ABC):
    """Abstract base class for reranking strategies."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """
        Rerank retrieval results.

        Args:
            query: Original query
            results: Retrieved results to rerank
            top_k: Optional limit on results to return

        Returns:
            Reranked list of results
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        pass
