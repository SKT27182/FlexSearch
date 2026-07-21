"""Preflight limits shared by every untrusted document parser."""

from __future__ import annotations

import io

from PIL import Image
from pypdf import PdfReader

from app.core.config import settings


def validate_document_limits(content: bytes, content_type: str) -> None:
    if content_type == "application/pdf":
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        if len(reader.pages) > settings.pdf_max_pages:
            raise ValueError(
                f"PDF has {len(reader.pages)} pages; limit is {settings.pdf_max_pages}"
            )
    elif content_type.startswith("image/"):
        with Image.open(io.BytesIO(content)) as image:
            pixels = image.width * image.height
            if pixels > settings.image_max_pixels:
                raise ValueError(
                    f"Image has {pixels} decoded pixels; limit is {settings.image_max_pixels}"
                )
            image.verify()
