"""Bounded upload spooling and authoritative supported-format validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import BinaryIO

from fastapi import HTTPException, UploadFile, status


@dataclass(frozen=True)
class SupportedFormat:
    content_type: str
    extensions: tuple[str, ...]


SUPPORTED_FORMATS = (
    SupportedFormat("application/pdf", (".pdf",)),
    SupportedFormat("text/plain", (".txt",)),
    SupportedFormat("text/markdown", (".md", ".markdown")),
    SupportedFormat("text/html", (".html", ".htm")),
    SupportedFormat("image/png", (".png",)),
    SupportedFormat("image/jpeg", (".jpg", ".jpeg")),
)
_BY_MIME = {fmt.content_type: fmt for fmt in SUPPORTED_FORMATS}
_BY_EXTENSION = {
    extension: fmt for fmt in SUPPORTED_FORMATS for extension in fmt.extensions
}


async def spool_upload(
    upload: UploadFile, *, max_bytes: int
) -> tuple[BinaryIO, int, bytes]:
    """Copy an upload into a bounded spooled file without buffering it all in RAM."""
    spool = SpooledTemporaryFile(max_size=min(max_bytes, 8 * 1024 * 1024))
    total = 0
    prefix = bytearray()
    while chunk := await upload.read(1024 * 1024):
        total += len(chunk)
        if total > max_bytes:
            spool.close()
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Upload exceeds the {max_bytes}-byte limit",
            )
        if len(prefix) < 8192:
            prefix.extend(chunk[: 8192 - len(prefix)])
        spool.write(chunk)
    spool.seek(0)
    return spool, total, bytes(prefix)


def validate_supported_upload(
    *, filename: str, declared_content_type: str | None, prefix: bytes
) -> str:
    """Return authoritative MIME or reject spoofed/unsupported content."""
    extension = Path(filename).suffix.lower()
    expected = _BY_EXTENSION.get(extension)
    declared = _BY_MIME.get((declared_content_type or "").lower())
    detected: str | None = None
    if prefix.startswith(b"%PDF-"):
        detected = "application/pdf"
    elif prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif prefix.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif b"\x00" not in prefix:
        try:
            text = prefix.decode("utf-8").lstrip().lower()
        except UnicodeDecodeError:
            text = ""
        if text:
            if extension in {".html", ".htm"} and (
                text.startswith("<!doctype html") or "<html" in text[:1024]
            ):
                detected = "text/html"
            elif extension in {".md", ".markdown"}:
                detected = "text/markdown"
            elif extension == ".txt":
                detected = "text/plain"
    if expected is None or declared is None or detected != expected.content_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File extension, MIME type, and content signature do not match a supported format",
        )
    return detected
