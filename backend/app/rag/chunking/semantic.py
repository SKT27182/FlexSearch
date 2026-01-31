"""
FlexSearch Backend - Semantic Chunking Strategy

Embedding-based semantic text splitting.
"""

from typing import Any

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.utils.logger import create_logger

logger = create_logger(__name__)


class SemanticChunking(BaseChunkingStrategy):
    """Semantic chunking based on embedding similarity."""

    def __init__(
        self,
        similarity_threshold: float = 0.5,
        min_chunk_size: int = 100,
        max_chunk_size: int = 1000,
    ) -> None:
        """
        Initialize semantic chunking.

        Args:
            similarity_threshold: Cosine similarity threshold for grouping
            min_chunk_size: Minimum chunk size in characters
            max_chunk_size: Maximum chunk size in characters
        """
        self._similarity_threshold = similarity_threshold
        self._min_chunk_size = min_chunk_size
        self._max_chunk_size = max_chunk_size
        self._model: SentenceTransformer | None = None

    def _get_model(self) -> SentenceTransformer:
        """Lazy load the embedding model."""
        if self._model is None:
            self._model = SentenceTransformer(settings.embedding_model)
        return self._model

    @property
    def name(self) -> str:
        return "semantic"

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Split text based on semantic similarity."""
        if not text.strip():
            return []

        # Split into sentences first
        sentences = self._split_sentences(text)
        if not sentences:
            return []

        # Get embeddings for each sentence
        model = self._get_model()
        embeddings = model.encode(sentences, convert_to_numpy=True)

        # Group sentences by semantic similarity
        groups = self._group_by_similarity(sentences, embeddings)

        # Create chunks from groups
        chunks = []
        current_pos = 0

        for idx, group in enumerate(groups):
            chunk_text = " ".join(group).strip()
            if not chunk_text:
                continue

            start = text.find(chunk_text[:50], current_pos)
            if start == -1:
                start = current_pos
            end = start + len(chunk_text)

            chunks.append(
                Chunk(
                    content=chunk_text,
                    document_id=document_id,
                    chunk_index=idx,
                    start_char=start,
                    end_char=end,
                    metadata=metadata or {},
                )
            )
            current_pos = start + 1

        logger.debug(
            f"Created {len(chunks)} semantic chunks from document {document_id}"
        )
        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        import re

        # Simple sentence splitting
        sentences = re.split(r"(?<=[.!?])\s+", text)
        return [s.strip() for s in sentences if s.strip()]

    def _cosine_similarity(
        self,
        a: np.ndarray,
        b: np.ndarray,
    ) -> float:
        """Calculate cosine similarity between two vectors."""
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

    def _group_by_similarity(
        self,
        sentences: list[str],
        embeddings: np.ndarray,
    ) -> list[list[str]]:
        """Group sentences by semantic similarity."""
        if len(sentences) == 0:
            return []
        if len(sentences) == 1:
            return [sentences]

        groups: list[list[str]] = []
        current_group: list[str] = [sentences[0]]
        current_length = len(sentences[0])

        for i in range(1, len(sentences)):
            sentence = sentences[i]

            # Check similarity with previous sentence
            similarity = self._cosine_similarity(embeddings[i], embeddings[i - 1])

            # Check if should start new group
            new_length = current_length + len(sentence) + 1

            if (
                similarity >= self._similarity_threshold
                and new_length <= self._max_chunk_size
            ):
                current_group.append(sentence)
                current_length = new_length
            else:
                # Start new group if current meets minimum size
                if current_length >= self._min_chunk_size:
                    groups.append(current_group)
                    current_group = [sentence]
                    current_length = len(sentence)
                else:
                    # Keep adding to current group
                    current_group.append(sentence)
                    current_length += len(sentence) + 1

        # Add final group
        if current_group:
            groups.append(current_group)

        return groups
