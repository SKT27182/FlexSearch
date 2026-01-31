"""
FlexSearch Backend - Fixed Window Chunking Strategy

Simple fixed-size chunking with configurable overlap.
"""

from typing import Any

from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.utils.logger import create_logger

logger = create_logger(__name__)


class FixedWindowChunking(BaseChunkingStrategy):
    """Fixed-size window chunking with overlap."""

    def __init__(
        self,
        chunk_size: int = 512,
        overlap: int = 50,
    ) -> None:
        """
        Initialize fixed window chunking.

        Args:
            chunk_size: Maximum characters per chunk
            overlap: Character overlap between chunks
        """
        self._chunk_size = chunk_size
        self._overlap = overlap

    @property
    def name(self) -> str:
        return "fixed_window"

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Split text into fixed-size chunks with overlap."""
        if not text.strip():
            return []

        chunks = []
        text_len = len(text)
        start = 0
        chunk_index = 0

        while start < text_len:
            # Calculate end position
            end = min(start + self._chunk_size, text_len)

            # Try to break at whitespace if not at end
            if end < text_len:
                # Look for last whitespace in the chunk
                last_space = text.rfind(" ", start, end)
                if last_space > start:
                    end = last_space

            chunk_content = text[start:end].strip()

            if chunk_content:
                chunks.append(
                    Chunk(
                        content=chunk_content,
                        document_id=document_id,
                        chunk_index=chunk_index,
                        start_char=start,
                        end_char=end,
                        metadata=metadata or {},
                    )
                )
                chunk_index += 1

            # Move start position with overlap
            start = end - self._overlap if end < text_len else text_len

            # Prevent infinite loop
            if start >= end:
                break

        logger.debug(f"Created {len(chunks)} chunks from document {document_id}")
        return chunks
