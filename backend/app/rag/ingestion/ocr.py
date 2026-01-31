"""
FlexSearch Backend - OCR Extraction Strategy

Tesseract-based OCR for text extraction from PDFs and images.
"""

import io
from typing import Any

import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from pypdf import PdfReader

from app.rag.ingestion.base import BaseExtractionStrategy, ExtractedContent
from app.utils.logger import create_logger

logger = create_logger(__name__)


class OCRExtractionStrategy(BaseExtractionStrategy):
    """OCR-based extraction using Tesseract."""

    SUPPORTED_TYPES = {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "text/plain",
        "text/markdown",
    }

    @property
    def name(self) -> str:
        return "ocr"

    def supports(self, content_type: str) -> bool:
        return content_type in self.SUPPORTED_TYPES

    async def extract(
        self,
        content: bytes,
        content_type: str,
        filename: str,
    ) -> ExtractedContent:
        """Extract text using OCR when needed."""
        logger.info(f"Extracting content from {filename} ({content_type})")

        if content_type in {"text/plain", "text/markdown"}:
            return self._extract_text(content, filename)
        elif content_type == "application/pdf":
            return await self._extract_pdf(content, filename)
        elif content_type.startswith("image/"):
            return await self._extract_image(content, filename)
        else:
            raise ValueError(f"Unsupported content type: {content_type}")

    def _extract_text(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract plain text files."""
        text = content.decode("utf-8", errors="replace")
        return ExtractedContent(
            text=text,
            metadata={"filename": filename, "extraction_method": "direct"},
            page_count=1,
        )

    async def _extract_pdf(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract text from PDF, using OCR for image-based pages."""
        all_text = []
        images = []
        page_count = 0

        try:
            # First, try direct text extraction
            reader = PdfReader(io.BytesIO(content))
            page_count = len(reader.pages)

            for i, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""

                # If page has minimal text, likely image-based - use OCR
                if len(page_text.strip()) < 50:
                    logger.debug(f"Page {i+1} appears image-based, using OCR")
                    # Convert PDF page to image and OCR
                    page_images = convert_from_bytes(
                        content,
                        first_page=i + 1,
                        last_page=i + 1,
                        dpi=200,
                    )
                    if page_images:
                        ocr_text = pytesseract.image_to_string(page_images[0])
                        all_text.append(ocr_text)
                        # Store image bytes if needed
                        img_bytes = io.BytesIO()
                        page_images[0].save(img_bytes, format="PNG")
                        images.append(img_bytes.getvalue())
                else:
                    all_text.append(page_text)

        except Exception as e:
            logger.warning(f"PDF text extraction failed, falling back to full OCR: {e}")
            # Full OCR fallback
            all_text = []
            try:
                pdf_images = convert_from_bytes(content, dpi=200)
                page_count = len(pdf_images)
                for img in pdf_images:
                    ocr_text = pytesseract.image_to_string(img)
                    all_text.append(ocr_text)
            except Exception as ocr_error:
                logger.error(f"OCR extraction also failed: {ocr_error}")
                raise

        return ExtractedContent(
            text="\n\n".join(all_text),
            metadata={
                "filename": filename,
                "extraction_method": "pdf_with_ocr",
            },
            images=images,
            page_count=page_count,
        )

    async def _extract_image(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract text from image using OCR."""
        image = Image.open(io.BytesIO(content))

        # Run OCR
        text = pytesseract.image_to_string(image)

        return ExtractedContent(
            text=text,
            metadata={
                "filename": filename,
                "extraction_method": "ocr",
                "image_size": f"{image.width}x{image.height}",
            },
            images=[content],
            page_count=1,
        )
