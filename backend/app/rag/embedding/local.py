"""
FlexSearch Backend - Local Embedding Service

Local embedding generation using sentence-transformers.
"""

import logging

from sentence_transformers import SentenceTransformer

from app.core.config import settings

logger = logging.getLogger(__name__)


class LocalEmbedding:
    """Local embedding service using sentence-transformers."""

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._model_name = settings.embedding_model

    def _get_model(self) -> SentenceTransformer:
        """Lazy load the embedding model."""
        if self._model is None:
            logger.info(f"Loading embedding model: {self._model_name}")
            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        model = self._get_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        if not texts:
            return []
        model = self._get_model()
        embeddings = model.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()

    @property
    def dimension(self) -> int:
        """Get embedding dimension."""
        model = self._get_model()
        return model.get_sentence_embedding_dimension()

    @property
    def model_name(self) -> str:
        """Get model name."""
        return self._model_name


# Singleton instance
_embedding_service: LocalEmbedding | None = None


def get_embedding_service() -> LocalEmbedding:
    """Get embedding service singleton."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = LocalEmbedding()
    return _embedding_service
