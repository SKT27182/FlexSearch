"""
FlexSearch Backend - Base Extraction Strategy

Abstract base class for document extraction strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExtractedContent:
    """Result of document extraction."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    images: list[bytes] = field(default_factory=list)  # Extracted images if any
    page_count: int = 1

    @property
    def is_empty(self) -> bool:
        """Check if extraction produced any content."""
        return not self.text.strip()


class BaseExtractionStrategy(ABC):
    """Abstract base class for extraction strategies."""

    @abstractmethod
    async def extract(
        self,
        content: bytes,
        content_type: str,
        filename: str,
    ) -> ExtractedContent:
        """
        Extract text content from a document.

        Args:
            content: Raw file bytes
            content_type: MIME type
            filename: Original filename

        Returns:
            ExtractedContent with text and metadata
        """
        pass

    @abstractmethod
    def supports(self, content_type: str) -> bool:
        """Check if this strategy supports the given content type."""
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name for logging/config."""
        pass
