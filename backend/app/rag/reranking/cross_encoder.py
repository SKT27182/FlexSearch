"""
FlexSearch Backend - Cross-Encoder Reranking Strategy

High-quality reranking using cross-encoder models.
"""

import logging

from sentence_transformers import CrossEncoder

from app.rag.reranking.base import BaseRerankingStrategy
from app.rag.retrieval.base import RetrievalResult

logger = logging.getLogger(__name__)

# Default cross-encoder model
DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"


class CrossEncoderReranking(BaseRerankingStrategy):
    """Cross-encoder based reranking for high quality."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: CrossEncoder | None = None

    def _get_model(self) -> CrossEncoder:
        """Lazy load the cross-encoder model."""
        if self._model is None:
            logger.info(f"Loading cross-encoder model: {self._model_name}")
            self._model = CrossEncoder(self._model_name)
        return self._model

    @property
    def name(self) -> str:
        return "cross_encoder"

    async def rerank(
        self,
        query: str,
        results: list[RetrievalResult],
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Rerank using cross-encoder scores."""
        if not results:
            return results

        model = self._get_model()

        # Create query-document pairs
        pairs = [(query, result.content) for result in results]

        # Get cross-encoder scores
        scores = model.predict(pairs)

        # Combine results with new scores
        scored_results = list(zip(results, scores))
        scored_results.sort(key=lambda x: x[1], reverse=True)

        # Update scores in results
        reranked = []
        for result, score in scored_results:
            result.score = float(score)
            result.metadata["rerank_score"] = float(score)
            reranked.append(result)

        if top_k is not None:
            reranked = reranked[:top_k]

        logger.debug(f"Reranked {len(results)} results, returning {len(reranked)}")
        return reranked
