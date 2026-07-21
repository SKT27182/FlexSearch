"""
FlexSearch Backend - VLM Extraction Strategy

Vision Language Model-based extraction for images and PDFs.
"""

import asyncio
import base64
import io
from typing import Any

from pdf2image import convert_from_bytes
from PIL import Image

from app.rag.ingestion.base import (
    BaseExtractionStrategy,
    ExtractedContent,
    ExtractionProgressCallback,
)
from app.rag.ingestion.document_limits import validate_document_limits
from app.services.llm import get_llm_service
from app.utils.logger import create_logger

logger = create_logger(__name__)

# Keep payloads small — large base64 images slow vision APIs dramatically.
VLM_MAX_IMAGE_SIDE = 1280
VLM_PDF_DPI = 120


class VLMExtractionStrategy(BaseExtractionStrategy):
    """VLM-based extraction using vision models for images and PDFs (per-page)."""

    SUPPORTED_TYPES = {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "text/plain",
        "text/markdown",
        "text/html",
    }

    VLM_PROMPT = """Analyze this image and extract all text content. 
If it's a document, preserve the structure including:
- Headers and titles
- Paragraphs
- Lists and bullet points
- Tables (as markdown)
- Any other structured content

Return only the extracted text, formatted cleanly."""

    @property
    def name(self) -> str:
        return "vlm"

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
        """Extract text using Vision Language Model."""
        logger.info("VLM extracting content from %s (%s)", filename, content_type)
        validate_document_limits(content, content_type)

        if content_type in {"text/plain", "text/markdown"}:
            text = content.decode("utf-8", errors="replace")
            return ExtractedContent(
                text=text,
                metadata={"filename": filename, "extraction_method": "direct"},
                page_count=1,
            )
        if content_type == "text/html":
            from app.services.website.content_extractor import extract_clean_content

            html = content.decode("utf-8", errors="replace")
            text = extract_clean_content(html) or html
            return ExtractedContent(
                text=text,
                metadata={"filename": filename, "extraction_method": "html"},
                page_count=1,
            )
        if content_type == "application/pdf":
            return await self._extract_pdf(content, filename, on_progress=on_progress)
        if content_type.startswith("image/"):
            return await self._extract_image(content, filename)
        raise ValueError(f"Unsupported content type: {content_type}")

    def _prepare_image_bytes(self, content: bytes) -> tuple[bytes, str]:
        """Downscale image for faster vision API calls."""
        image = Image.open(io.BytesIO(content))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        w, h = image.size
        longest = max(w, h)
        if longest > VLM_MAX_IMAGE_SIDE:
            scale = VLM_MAX_IMAGE_SIDE / longest
            image = image.resize(
                (int(w * scale), int(h * scale)),
                Image.Resampling.LANCZOS,
            )
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue(), "image/jpeg"

    async def _extract_image(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract text from image using VLM."""
        img_bytes, mime_type = self._prepare_image_bytes(content)
        base64_image = base64.b64encode(img_bytes).decode("utf-8")

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_image}",
                        },
                    },
                    {"type": "text", "text": self.VLM_PROMPT},
                ],
            }
        ]

        llm = get_llm_service()
        try:
            response = await llm.complete(
                messages,
                max_tokens=4096,
                timeout_sec=60.0,
            )
            extracted_text = response.content
        except Exception as e:
            logger.error("VLM extraction failed for %s: %s", filename, e)
            raise

        image = Image.open(io.BytesIO(content))
        return ExtractedContent(
            text=extracted_text,
            metadata={
                "filename": filename,
                "extraction_method": "vlm",
                "image_size": f"{image.width}x{image.height}",
            },
            images=[content],
            page_count=1,
        )

    async def _extract_pdf(
        self,
        content: bytes,
        filename: str,
        *,
        on_progress: ExtractionProgressCallback | None = None,
    ) -> ExtractedContent:
        """Extract text from PDF by converting each page to an image and calling VLM."""
        if on_progress:
            await on_progress("Converting PDF to images…", None, None)

        pdf_images = await asyncio.to_thread(
            convert_from_bytes,
            content,
            dpi=VLM_PDF_DPI,
        )
        page_count = len(pdf_images)
        logger.info("VLM PDF %s: %d page(s)", filename, page_count)

        all_text: list[str] = []
        images: list[bytes] = []

        for i, page_image in enumerate(pdf_images):
            page_num = i + 1
            if on_progress:
                await on_progress(
                    f"Extracting page {page_num}/{page_count} with VLM…",
                    page_num,
                    page_count,
                )
            logger.info(
                "VLM PDF %s: processing page %d/%d", filename, page_num, page_count
            )

            img_buffer = io.BytesIO()
            page_image.save(img_buffer, format="PNG")
            img_bytes = img_buffer.getvalue()

            page_result = await self._extract_image(
                img_bytes,
                f"{filename}_page_{page_num}",
            )
            all_text.append(f"--- Page {page_num} ---\n{page_result.text}")
            images.append(img_bytes)

        return ExtractedContent(
            text="\n\n".join(all_text),
            metadata={
                "filename": filename,
                "extraction_method": "vlm_pdf",
            },
            images=images,
            page_count=page_count,
        )
