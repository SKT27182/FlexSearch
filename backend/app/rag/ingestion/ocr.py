"""
FlexSearch Backend - OCR Extraction Strategy

Tesseract-based OCR for text extraction from PDFs and images.
"""

import asyncio
import io
import shutil

import pytesseract
from pdf2image import convert_from_bytes
from PIL import Image
from pypdf import PdfReader

from app.rag.ingestion.base import (
    BaseExtractionStrategy,
    ExtractedContent,
    ExtractionProgressCallback,
)
from app.rag.ingestion.document_limits import validate_document_limits
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
        "text/html",
    }

    def __init__(self):
        super().__init__()
        self._check_dependencies()

    def _check_dependencies(self):
        """Check if required system binaries are available."""
        if not shutil.which("tesseract"):
            logger.error(
                "Tesseract binary not found in PATH. "
                "OCR features will be disabled. "
                "Please install tesseract-ocr (e.g., 'sudo apt install tesseract-ocr' or 'brew install tesseract')."
            )

        if not shutil.which("pdftoppm"):
            logger.warning(
                "pdftoppm (poppler-utils) not found in PATH. "
                "PDF OCR fallback will be disabled. "
                "Please install poppler-utils (e.g., 'sudo apt install poppler-utils' or 'brew install poppler')."
            )

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
        *,
        on_progress: ExtractionProgressCallback | None = None,
    ) -> ExtractedContent:
        """Extract text using OCR when needed."""
        logger.info("Extracting content from %s (%s)", filename, content_type)
        validate_document_limits(content, content_type)

        if content_type in {"text/plain", "text/markdown"}:
            return self._extract_text(content, filename)
        if content_type == "text/html":
            from app.services.website.content_extractor import extract_clean_content

            html = content.decode("utf-8", errors="replace")
            md = extract_clean_content(html) or html
            return ExtractedContent(
                text=md, page_count=1, metadata={"filename": filename}
            )
        if content_type == "application/pdf":
            if on_progress:
                await on_progress("Extracting text from PDF…", None, None)
            return await asyncio.to_thread(self._extract_pdf, content, filename)
        if content_type.startswith("image/"):
            return await asyncio.to_thread(self._extract_image, content, filename)
        raise ValueError(f"Unsupported content type: {content_type}")

    def _extract_text(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract plain text files."""
        text = content.decode("utf-8", errors="replace")
        return ExtractedContent(
            text=text,
            metadata={"filename": filename, "extraction_method": "direct"},
            page_count=1,
        )

    def _extract_pdf(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract text from PDF, using OCR for image-based pages."""
        if not shutil.which("tesseract"):
            raise RuntimeError(
                "Tesseract OCR is not installed or not in PATH. "
                "Please install 'tesseract-ocr' on your system."
            )

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
                    logger.debug(f"Page {i + 1} appears image-based, using OCR")
                    # Convert PDF page to image and OCR
                    page_images = convert_from_bytes(
                        content,
                        first_page=i + 1,
                        last_page=i + 1,
                        dpi=200,
                        timeout=60,
                    )
                    if page_images:
                        ocr_text = pytesseract.image_to_string(
                            page_images[0], timeout=60
                        )
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
                reader = PdfReader(io.BytesIO(content))
                page_count = len(reader.pages)
                for page_number in range(1, page_count + 1):
                    page_images = convert_from_bytes(
                        content,
                        dpi=200,
                        first_page=page_number,
                        last_page=page_number,
                        timeout=60,
                    )
                    if page_images:
                        all_text.append(
                            pytesseract.image_to_string(page_images[0], timeout=60)
                        )
            except Exception:
                logger.exception("OCR extraction failed")
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

    def _extract_image(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract text from image using OCR."""
        if not shutil.which("tesseract"):
            raise RuntimeError(
                "Tesseract OCR is not installed or not in PATH. "
                "Please install 'tesseract-ocr' on your system."
            )

        image = Image.open(io.BytesIO(content))

        # Run OCR
        text = pytesseract.image_to_string(image, timeout=60)

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
