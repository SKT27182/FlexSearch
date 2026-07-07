"""FlexSearch RAG embedding backends (local sentence-transformers)."""

from app.rag.embedding.local import LocalEmbedding, LocalEmbeddingBackend

__all__ = [
    "LocalEmbedding",
    "LocalEmbeddingBackend",
]
