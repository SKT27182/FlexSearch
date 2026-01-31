"""
FlexSearch Backend - Base Chunking Strategy

Abstract base class for text chunking strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """A text chunk with metadata for RAG."""

    content: str
    document_id: str
    chunk_index: int
    start_char: int
    end_char: int
    metadata: dict[str, Any] = field(default_factory=dict)
    parent_id: str | None = None  # For parent-child chunking

    @property
    def char_count(self) -> int:
        """Number of characters in the chunk."""
        return len(self.content)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "content": self.content,
            "document_id": self.document_id,
            "chunk_index": self.chunk_index,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "parent_id": self.parent_id,
            **self.metadata,
        }


class BaseChunkingStrategy(ABC):
    """Abstract base class for chunking strategies."""

    @abstractmethod
    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """
        Split text into chunks.

        Args:
            text: Full document text
            document_id: Unique document identifier
            metadata: Additional metadata to attach to chunks

        Returns:
            List of Chunk objects
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name for logging/config."""
        pass
