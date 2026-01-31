"""
FlexSearch Backend - Recursive Chunking Strategy

Structure-aware recursive text splitting.
"""

import re
from typing import Any

from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.utils.logger import create_logger

logger = create_logger(__name__)


class RecursiveChunking(BaseChunkingStrategy):
    """Recursive text splitting with hierarchical separators."""

    DEFAULT_SEPARATORS = [
        "\n\n\n",  # Multiple newlines (major sections)
        "\n\n",  # Paragraph breaks
        "\n",  # Line breaks
        ". ",  # Sentences
        ", ",  # Clauses
        " ",  # Words
        "",  # Characters (fallback)
    ]

    def __init__(
        self,
        chunk_size: int = 512,
        overlap: int = 50,
        separators: list[str] | None = None,
    ) -> None:
        """
        Initialize recursive chunking.

        Args:
            chunk_size: Target chunk size in characters
            overlap: Character overlap between chunks
            separators: Ordered list of separators to try
        """
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._separators = separators or self.DEFAULT_SEPARATORS

    @property
    def name(self) -> str:
        return "recursive"

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Split text recursively using hierarchical separators."""
        if not text.strip():
            return []

        chunks = self._split_recursive(text, self._separators)

        # Merge small chunks and split large ones
        merged = self._merge_chunks(chunks)

        # Create Chunk objects
        result = []
        current_pos = 0

        for idx, chunk_text in enumerate(merged):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                continue

            start = text.find(chunk_text, current_pos)
            if start == -1:
                start = current_pos
            end = start + len(chunk_text)

            result.append(
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

        logger.debug(f"Created {len(result)} chunks from document {document_id}")
        return result

    def _split_recursive(
        self,
        text: str,
        separators: list[str],
    ) -> list[str]:
        """Recursively split text using separators."""
        if not separators:
            return [text] if text else []

        separator = separators[0]
        remaining_separators = separators[1:]

        if not separator:
            # Character-level split
            return list(text)

        # Split by current separator
        parts = text.split(separator)

        result = []
        for part in parts:
            if len(part) <= self._chunk_size:
                result.append(part)
            else:
                # Part is too large, split with next separator
                result.extend(self._split_recursive(part, remaining_separators))

        return result

    def _merge_chunks(self, chunks: list[str]) -> list[str]:
        """Merge small chunks and ensure proper sizing."""
        if not chunks:
            return []

        result = []
        current = ""

        for chunk in chunks:
            if not chunk.strip():
                continue

            if len(current) + len(chunk) + 1 <= self._chunk_size:
                current = f"{current} {chunk}".strip() if current else chunk
            else:
                if current:
                    result.append(current)

                # If chunk itself is too large, split it
                if len(chunk) > self._chunk_size:
                    # Simple split at chunk_size
                    for i in range(0, len(chunk), self._chunk_size - self._overlap):
                        result.append(chunk[i : i + self._chunk_size])
                    current = ""
                else:
                    current = chunk

        if current:
            result.append(current)

        return result
