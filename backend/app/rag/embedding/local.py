"""
FlexSearch Backend - Local Embedding Backend

sentence-transformers backend used by EmbeddingService.
"""

from __future__ import annotations

from sentence_transformers import SentenceTransformer

from app.utils.logger import create_logger

logger = create_logger(__name__)


class LocalEmbeddingBackend:
    """Local embedding backend using sentence-transformers."""

    def __init__(self, model_name: str) -> None:
        self._model: SentenceTransformer | None = None
        self._model_name = model_name

    def _get_model(self) -> SentenceTransformer:
        if self._model is None:
            logger.info("Loading embedding model: %s", self._model_name)
            # Model loaders otherwise write progress bars straight to stderr,
            # bypassing the application logger and looking like warnings under
            # Celery. Normal load/failure messages still use logging.
            from huggingface_hub.utils import disable_progress_bars
            from transformers.utils.logging import disable_progress_bar

            disable_progress_bars()
            disable_progress_bar()
            try:
                # Prefer the cache. This avoids an unnecessary Hub request (and
                # its unauthenticated-download warning) on normal worker starts.
                self._model = SentenceTransformer(
                    self._model_name,
                    local_files_only=True,
                )
            except OSError:
                logger.info(
                    "Embedding model not cached; downloading %s", self._model_name
                )
                self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, text: str) -> list[float]:
        model = self._get_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        embeddings = model.encode(texts, convert_to_numpy=True)
        return embeddings.tolist()

    @property
    def dimension(self) -> int:
        model = self._get_model()
        return model.get_embedding_dimension()

    @property
    def model_name(self) -> str:
        return self._model_name


# Backward-compatible alias
LocalEmbedding = LocalEmbeddingBackend
