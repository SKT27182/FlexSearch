"""
FlexSearch Backend - Base Retrieval Strategy

Abstract base class for retrieval strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RetrievalResult:
    """Result from retrieval."""

    content: str
    score: float
    document_id: str
    chunk_id: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "content": self.content,
            "score": self.score,
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            **self.metadata,
        }


class BaseRetrievalStrategy(ABC):
    """Abstract base class for retrieval strategies."""

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
        rag_generation: int | None = None,
    ) -> list[RetrievalResult]:
        """
        Retrieve relevant chunks for a query.

        Args:
            query: User query
            project_id: Project to search within
            top_k: Number of results to return
            rag_generation: Published project generation to search

        Returns:
            List of RetrievalResult objects
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        pass
