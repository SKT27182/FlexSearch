"""
Hybrid PDF extraction: native text via pypdf, OCR fallback for sparse pages.
"""

from __future__ import annotations

import asyncio
import io
import shutil
from typing import Any

import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from pypdf import PdfReader

from app.rag.ingestion.base import (
    BaseExtractionStrategy,
    ExtractedContent,
    ExtractionProgressCallback,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)

# Pages with fewer than this many non-whitespace chars trigger OCR fallback
_MIN_NATIVE_CHARS = 40


class HybridPdfExtractionStrategy(BaseExtractionStrategy):
    """Prefer embedded PDF text; OCR pages that look empty/scanned."""

    SUPPORTED_TYPES = {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "text/plain",
        "text/markdown",
        "text/html",
    }

    def __init__(self, *, min_native_chars: int = _MIN_NATIVE_CHARS) -> None:
        self._min_native_chars = min_native_chars
        if not shutil.which("tesseract"):
            logger.warning(
                "Tesseract not found; hybrid_pdf OCR fallback will be limited"
            )

    @property
    def name(self) -> str:
        return "hybrid_pdf"

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
        logger.info("Hybrid PDF extract %s (%s)", filename, content_type)

        if content_type in {"text/plain", "text/markdown"}:
            text = content.decode("utf-8", errors="replace")
            return ExtractedContent(
                text=text,
                metadata={"filename": filename, "strategy": self.name},
                page_count=1,
            )

        if content_type == "text/html":
            from app.services.website.content_extractor import extract_clean_content

            html = content.decode("utf-8", errors="replace")
            text = extract_clean_content(html) or html
            return ExtractedContent(
                text=text,
                metadata={"filename": filename, "strategy": self.name},
                page_count=1,
            )

        if content_type.startswith("image/"):
            text = await asyncio.to_thread(self._ocr_image, content)
            return ExtractedContent(
                text=text,
                metadata={"filename": filename, "strategy": self.name, "ocr": True},
                page_count=1,
            )

        return await asyncio.to_thread(
            self._extract_pdf_sync, content, filename, on_progress
        )

    def _extract_pdf_sync(
        self,
        content: bytes,
        filename: str,
        on_progress: ExtractionProgressCallback | None,
    ) -> ExtractedContent:
        reader = PdfReader(io.BytesIO(content))
        page_count = len(reader.pages)
        page_texts: list[str] = []
        ocr_pages: list[int] = []

        for i, page in enumerate(reader.pages):
            native = (page.extract_text() or "").strip()
            if len(native) >= self._min_native_chars:
                page_texts.append(native)
            else:
                ocr_pages.append(i)
                page_texts.append("")  # placeholder

        if ocr_pages and shutil.which("tesseract") and shutil.which("pdftoppm"):
            images = convert_from_bytes(content, dpi=200)
            for idx in ocr_pages:
                if idx < len(images):
                    page_texts[idx] = pytesseract.image_to_string(images[idx]) or ""

        # Progress callback is async; fire-and-forget via stored loop is awkward
        # in sync helper — document_worker already updates status around extract.
        del on_progress

        text = "\n\n".join(t.strip() for t in page_texts if t.strip())
        meta: dict[str, Any] = {
            "filename": filename,
            "strategy": self.name,
            "ocr_page_indexes": ocr_pages,
            "native_pages": page_count - len(ocr_pages),
        }
        return ExtractedContent(text=text, metadata=meta, page_count=page_count)

    def _ocr_image(self, content: bytes) -> str:
        image = Image.open(io.BytesIO(content))
        return pytesseract.image_to_string(image) or ""
