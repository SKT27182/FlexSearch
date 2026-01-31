"""FlexSearch Backend - Embedding service."""

from app.rag.embedding.local import LocalEmbedding, get_embedding_service

__all__ = ["LocalEmbedding", "get_embedding_service"]
