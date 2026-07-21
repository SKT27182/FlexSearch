"""
FlexSearch Backend - Embedding Service

Routes to local sentence-transformers or LiteLLM API embeddings based on model id.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from app.core.config import settings
from app.rag.embedding.local import LocalEmbeddingBackend
from app.services.litellm_config import (
    configure_litellm,
    graphrag_embedding_endpoint,
    vector_embedding_endpoint,
)
from litellm import (
    aembedding,
    embedding,
)  # after litellm_config installs AWS-preload filter

from app.services.model_ids import extract_litellm_provider, is_local_embedding_model
from app.utils.logger import create_logger

logger = create_logger(__name__, level=settings.log_level)


@dataclass
class EmbeddingResult:
    """Embedding batch result with metadata."""

    vectors: list[list[float]]
    model: str
    provider: str
    latency_ms: int


class EmbeddingService:
    """Unified embedding service (local sentence-transformers or LiteLLM API)."""

    def __init__(
        self,
        *,
        model_id: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        configure_litellm()
        self._model = model_id or settings.embedding_model
        self._api_key = (
            api_key
            if api_key is not None
            else (settings.embedding_api_key or settings.api_key)
        )
        self._api_base = api_base
        self._provider = extract_litellm_provider(self._model)
        self._local: LocalEmbeddingBackend | None = None
        self._dimension: int | None = None

    @property
    def uses_local_model(self) -> bool:
        return is_local_embedding_model(self._model)

    def _local_backend(self) -> LocalEmbeddingBackend:
        if self._local is None:
            self._local = LocalEmbeddingBackend(self._model)
        return self._local

    def embed(self, text: str) -> list[float]:
        vectors = self.embed_batch([text])
        return vectors[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.uses_local_model:
            return self._local_backend().embed_batch(texts)
        return self._embed_batch_api(texts).vectors

    async def embed_async(self, text: str) -> list[float]:
        vectors = await self.embed_batch_async([text])
        return vectors[0]

    async def embed_batch_async(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.uses_local_model:
            return await asyncio.to_thread(self._local_backend().embed_batch, texts)
        result = await self._embed_batch_api_async(texts)
        return result.vectors

    def _embed_batch_api(self, texts: list[str]) -> EmbeddingResult:
        start = time.time()
        try:
            response = embedding(
                model=self._model,
                input=texts,
                api_key=self._api_key or None,
                api_base=self._api_base,
            )
            vectors = [item["embedding"] for item in response.data]
            latency_ms = int((time.time() - start) * 1000)
            logger.verbose(
                "Embedding batch: model=%s count=%d latency_ms=%d",
                self._model,
                len(texts),
                latency_ms,
            )
            return EmbeddingResult(
                vectors=vectors,
                model=self._model,
                provider=self._provider,
                latency_ms=latency_ms,
            )
        except Exception:
            logger.exception("Embedding request failed (model=%s)", self._model)
            raise

    async def _embed_batch_api_async(self, texts: list[str]) -> EmbeddingResult:
        start = time.time()
        try:
            response = await aembedding(
                model=self._model,
                input=texts,
                api_key=self._api_key or None,
                api_base=self._api_base,
            )
            vectors = [item["embedding"] for item in response.data]
            latency_ms = int((time.time() - start) * 1000)
            return EmbeddingResult(
                vectors=vectors,
                model=self._model,
                provider=self._provider,
                latency_ms=latency_ms,
            )
        except Exception:
            logger.exception("Embedding request failed (model=%s)", self._model)
            raise

    @property
    def dimension(self) -> int:
        if self._dimension is not None:
            return self._dimension
        if self.uses_local_model:
            self._dimension = self._local_backend().dimension
        else:
            self._dimension = len(self.embed("dimension probe"))
        return self._dimension

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._provider


_embedding_service: EmbeddingService | None = None
_graphrag_embedding_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Vector RAG embedding service (EMBEDDING_MODEL)."""
    global _embedding_service
    if _embedding_service is None:
        ep = vector_embedding_endpoint()
        _embedding_service = EmbeddingService(
            model_id=ep.model,
            api_key=ep.api_key,
            api_base=ep.api_base,
        )
    return _embedding_service


def get_graphrag_embedding_service() -> EmbeddingService:
    """Microsoft GraphRAG embedding service (GRAPHRAG_EMBEDDING_MODEL)."""
    global _graphrag_embedding_service
    if _graphrag_embedding_service is None:
        ep = graphrag_embedding_endpoint()
        _graphrag_embedding_service = EmbeddingService(
            model_id=ep.model,
            api_key=ep.api_key,
            api_base=ep.api_base,
        )
    return _graphrag_embedding_service
