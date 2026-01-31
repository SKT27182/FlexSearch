"""
FlexSearch Backend - Parent-Child Chunking Strategy

Hierarchical chunking for better context retrieval.
"""

import logging
import uuid
from typing import Any

from app.rag.chunking.base import BaseChunkingStrategy, Chunk

logger = logging.getLogger(__name__)


class ParentChildChunking(BaseChunkingStrategy):
    """Parent-child hierarchical chunking."""

    def __init__(
        self,
        parent_chunk_size: int = 1500,
        child_chunk_size: int = 300,
        overlap: int = 50,
    ) -> None:
        """
        Initialize parent-child chunking.

        Args:
            parent_chunk_size: Size of parent (context) chunks
            child_chunk_size: Size of child (retrieval) chunks
            overlap: Character overlap between chunks
        """
        self._parent_size = parent_chunk_size
        self._child_size = child_chunk_size
        self._overlap = overlap

    @property
    def name(self) -> str:
        return "parent_child"

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """
        Create hierarchical parent-child chunks.

        Returns both parent and child chunks. Child chunks reference their
        parent via parent_id. During retrieval, search on children and
        return parent content for context.
        """
        if not text.strip():
            return []

        chunks = []
        chunk_index = 0
        text_len = len(text)
        start = 0

        while start < text_len:
            # Create parent chunk
            parent_end = min(start + self._parent_size, text_len)

            # Try to break at paragraph or sentence
            if parent_end < text_len:
                for sep in ["\n\n", "\n", ". ", " "]:
                    last_sep = text.rfind(sep, start, parent_end)
                    if last_sep > start + (self._parent_size // 2):
                        parent_end = last_sep + len(sep)
                        break

            parent_content = text[start:parent_end].strip()

            if not parent_content:
                start = parent_end
                continue

            # Generate parent ID
            parent_id = str(uuid.uuid4())

            # Create parent chunk (marked as parent in metadata)
            parent_metadata = {
                **(metadata or {}),
                "is_parent": True,
                "chunk_type": "parent",
            }

            parent_chunk = Chunk(
                content=parent_content,
                document_id=document_id,
                chunk_index=chunk_index,
                start_char=start,
                end_char=parent_end,
                metadata=parent_metadata,
                parent_id=None,  # Parents don't have parent
            )
            # Store parent_id in metadata for reference
            parent_chunk.metadata["parent_chunk_id"] = parent_id
            chunks.append(parent_chunk)
            chunk_index += 1

            # Create child chunks within the parent
            child_start = 0
            while child_start < len(parent_content):
                child_end = min(child_start + self._child_size, len(parent_content))

                # Try to break at whitespace
                if child_end < len(parent_content):
                    last_space = parent_content.rfind(" ", child_start, child_end)
                    if last_space > child_start:
                        child_end = last_space

                child_content = parent_content[child_start:child_end].strip()

                if child_content:
                    child_metadata = {
                        **(metadata or {}),
                        "is_parent": False,
                        "chunk_type": "child",
                    }

                    child_chunk = Chunk(
                        content=child_content,
                        document_id=document_id,
                        chunk_index=chunk_index,
                        start_char=start + child_start,
                        end_char=start + child_end,
                        metadata=child_metadata,
                        parent_id=parent_id,  # Reference to parent
                    )
                    chunks.append(child_chunk)
                    chunk_index += 1

                # Move with overlap
                child_start = child_end - self._overlap
                if child_start >= child_end:
                    break

            # Move to next parent
            start = parent_end

        logger.debug(
            f"Created {len(chunks)} parent-child chunks from document {document_id}"
        )
        return chunks

    def get_parent_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Filter to only parent chunks."""
        return [c for c in chunks if c.metadata.get("is_parent", False)]

    def get_child_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Filter to only child chunks."""
        return [c for c in chunks if not c.metadata.get("is_parent", True)]
