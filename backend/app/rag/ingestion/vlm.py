"""
FlexSearch Backend - VLM Extraction Strategy

Vision Language Model-based extraction for images and PDFs.
"""

import base64
import io
from typing import Any

from pdf2image import convert_from_bytes
from PIL import Image

from app.core.config import settings
from app.rag.ingestion.base import BaseExtractionStrategy, ExtractedContent
from app.services.llm import get_llm_service
from app.utils.logger import create_logger

logger = create_logger(__name__)


class VLMExtractionStrategy(BaseExtractionStrategy):
    """VLM-based extraction using vision models."""

    SUPPORTED_TYPES = {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "text/plain",
        "text/markdown",
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
    ) -> ExtractedContent:
        """Extract text using Vision Language Model."""
        logger.info(f"VLM extracting content from {filename} ({content_type})")

        if content_type in {"text/plain", "text/markdown"}:
            # No need for VLM on plain text
            text = content.decode("utf-8", errors="replace")
            return ExtractedContent(
                text=text,
                metadata={"filename": filename, "extraction_method": "direct"},
                page_count=1,
            )
        elif content_type == "application/pdf":
            return await self._extract_pdf(content, filename)
        elif content_type.startswith("image/"):
            return await self._extract_image(content, filename)
        else:
            raise ValueError(f"Unsupported content type: {content_type}")

    async def _extract_image(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract text from image using VLM."""
        # Encode image to base64
        base64_image = base64.b64encode(content).decode("utf-8")

        # Determine image type
        image = Image.open(io.BytesIO(content))
        image_format = image.format or "PNG"
        mime_type = f"image/{image_format.lower()}"

        # Build message with image
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_image}"},
                    },
                    {
                        "type": "text",
                        "text": self.VLM_PROMPT,
                    },
                ],
            }
        ]

        # Call VLM
        llm = get_llm_service()

        try:
            # Note: This requires a vision-capable model
            response = await llm.complete(messages, max_tokens=4096)
            extracted_text = response.content
        except Exception as e:
            logger.error(f"VLM extraction failed: {e}")
            raise

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

    async def _extract_pdf(self, content: bytes, filename: str) -> ExtractedContent:
        """Extract text from PDF using VLM on each page."""
        all_text = []
        images = []

        try:
            # Convert PDF pages to images
            pdf_images = convert_from_bytes(content, dpi=150)
            page_count = len(pdf_images)

            for i, page_image in enumerate(pdf_images):
                logger.debug(f"Processing page {i+1}/{page_count}")

                # Convert PIL image to bytes
                img_buffer = io.BytesIO()
                page_image.save(img_buffer, format="PNG")
                img_bytes = img_buffer.getvalue()

                # Extract using VLM
                page_result = await self._extract_image(
                    img_bytes,
                    f"{filename}_page_{i+1}",
                )
                all_text.append(f"--- Page {i+1} ---\n{page_result.text}")
                images.append(img_bytes)

        except Exception as e:
            logger.error(f"VLM PDF extraction failed: {e}")
            raise

        return ExtractedContent(
            text="\n\n".join(all_text),
            metadata={
                "filename": filename,
                "extraction_method": "vlm_pdf",
            },
            images=images,
            page_count=len(pdf_images),
        )
