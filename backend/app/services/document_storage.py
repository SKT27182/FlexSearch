"""MinIO paths and helpers for document artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID


def raw_object_key(project_id: UUID | str, document_id: UUID | str, filename: str) -> str:
    ext = Path(filename).suffix or ".bin"
    return f"{project_id}/{document_id}/raw{ext}"


def extracted_md_key(project_id: UUID | str, document_id: UUID | str) -> str:
    return f"{project_id}/{document_id}/extracted.md"


def extracted_meta_key(project_id: UUID | str, document_id: UUID | str) -> str:
    return f"{project_id}/{document_id}/extracted.meta.json"


def build_extracted_meta(
    *,
    content_format: str,
    extraction_strategy: str,
    page_count: int,
    extraction_config_hash: str,
    content_type: str,
) -> dict:
    return {
        "content_format": content_format,
        "extraction_strategy": extraction_strategy,
        "page_count": page_count,
        "extraction_config_hash": extraction_config_hash,
        "content_type": content_type,
    }


def meta_to_bytes(meta: dict) -> bytes:
    return json.dumps(meta, indent=2).encode("utf-8")
