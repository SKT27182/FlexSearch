"""
Docling-based structure-aware document extraction.

Falls back with a clear error if the optional ``docling`` package is missing.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from app.rag.ingestion.base import (
    BaseExtractionStrategy,
    ExtractedContent,
    ExtractionProgressCallback,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)


class DoclingExtractionStrategy(BaseExtractionStrategy):
    """Extract markdown via IBM Docling (layout-aware)."""

    SUPPORTED_TYPES = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/markdown",
        "text/html",
        "image/png",
        "image/jpeg",
        "image/jpg",
    }

    @property
    def name(self) -> str:
        return "docling"

    def supports(self, content_type: str) -> bool:
        return content_type in self.SUPPORTED_TYPES

    async def extract(
        self,
        content: bytes,
        content_type: str,
        filename: str,
        *,
        on_progress: ExtractionProgressCallback | None = None,
    ) -> ExtractedContent:
        if content_type in {"text/plain", "text/markdown"}:
            text = content.decode("utf-8", errors="replace")
            return ExtractedContent(
                text=text,
                metadata={"filename": filename, "strategy": self.name},
                page_count=1,
            )

        if on_progress:
            await on_progress("Docling conversion…", None, None)

        return await asyncio.to_thread(
            self._convert_sync, content, filename, content_type
        )

    def _convert_sync(
        self,
        content: bytes,
        filename: str,
        content_type: str,
    ) -> ExtractedContent:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as exc:
            raise RuntimeError(
                "docling package is required for the docling extraction strategy. "
                "Install with: uv add docling"
            ) from exc

        suffix = Path(filename).suffix or _suffix_for_mime(content_type)
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(content)
            tmp.flush()
            converter = DocumentConverter()
            result = converter.convert(tmp.name)
            document = result.document
            text = document.export_to_markdown()
            page_count = 1
            try:
                pages = getattr(document, "pages", None)
                if pages is not None:
                    page_count = max(1, len(pages))
            except Exception:
                page_count = 1

        meta: dict[str, Any] = {
            "filename": filename,
            "strategy": self.name,
            "content_format": "markdown",
        }
        logger.info(
            "Docling extracted %d chars from %s (%d pages)",
            len(text),
            filename,
            page_count,
        )
        return ExtractedContent(text=text or "", metadata=meta, page_count=page_count)


def _suffix_for_mime(content_type: str) -> str:
    mapping = {
        "application/pdf": ".pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "text/html": ".html",
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
    }
    return mapping.get(content_type, ".bin")
