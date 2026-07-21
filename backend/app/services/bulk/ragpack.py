"""Parse / build .ragpack zip archives."""

from __future__ import annotations

import json
import stat
import zipfile
from io import BytesIO
from pathlib import Path

from app.core.config import settings

from app.services.bulk.schemas import (
    BulkImportManifest,
    FileDocumentReference,
    TextDocumentReference,
)


def safe_join(base_dir: Path, relative: str) -> Path:
    """Resolve ``relative`` under ``base_dir``; reject path traversal."""
    base = base_dir.resolve()
    # Disallow absolute paths and null bytes up front.
    if not relative or "\x00" in relative:
        raise ValueError(f"Invalid archive path: {relative!r}")
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Path escapes extract root: {relative}") from exc
    return candidate


def safe_extract_zip(zf: zipfile.ZipFile, extract_dir: str | Path) -> None:
    """Extract a bounded archive after traversal, type, and bomb checks."""
    root = Path(extract_dir).resolve()
    entries = zf.infolist()
    if len(entries) > settings.archive_max_entries:
        raise ValueError("Archive contains too many entries")
    expanded_total = 0
    for info in entries:
        name = info.filename
        mode = info.external_attr >> 16
        if info.flag_bits & 0x1:
            raise ValueError(f"Encrypted archive entry is not allowed: {name}")
        if stat.S_ISLNK(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
            raise ValueError(f"Special archive entry is not allowed: {name}")
        if Path(name).suffix.lower() in {".zip", ".ragpack"}:
            raise ValueError(f"Nested archive is not allowed: {name}")
        if info.file_size > settings.archive_member_max_bytes:
            raise ValueError(f"Archive member is too large: {name}")
        expanded_total += info.file_size
        if expanded_total > settings.archive_expanded_max_bytes:
            raise ValueError("Archive expanded size exceeds the configured limit")
        if (
            info.file_size / max(1, info.compress_size)
            > settings.archive_max_compression_ratio
        ):
            raise ValueError(f"Archive member compression ratio is excessive: {name}")
        if not name or name.endswith("/"):
            # Directory entries — still validate destination.
            dest = safe_join(root, name.rstrip("/") or ".")
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest = safe_join(root, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(dest, "wb") as out:
            member_written = 0
            while chunk := src.read(1024 * 1024):
                member_written += len(chunk)
                if member_written > settings.archive_member_max_bytes:
                    raise ValueError(f"Archive member is too large: {name}")
                out.write(chunk)
            if member_written != info.file_size:
                raise ValueError(f"Archive member size mismatch: {name}")


def find_manifest_dir(extract_root: Path) -> Path:
    """BFS for directory containing manifest.json (handles nested Mac zip folders)."""
    queue = [extract_root]
    while queue:
        current = queue.pop(0)
        candidate = current / "manifest.json"
        if candidate.exists():
            return current
        for child in current.iterdir():
            if child.is_dir():
                queue.append(child)
    raise ValueError("manifest.json not found in archive")


def load_manifest(manifest_dir: Path) -> BulkImportManifest:
    with open(manifest_dir / "manifest.json", encoding="utf-8") as f:
        data = json.load(f)
    return BulkImportManifest.model_validate(data)


def validate_referenced_files(manifest: BulkImportManifest, base_dir: Path) -> None:
    for project in manifest.projects:
        for doc in project.documents:
            if isinstance(doc, (FileDocumentReference, TextDocumentReference)):
                path = safe_join(base_dir, doc.path)
                if not path.exists():
                    raise ValueError(f"Referenced file not found: {doc.path}")


def build_ragpack_zip(
    *,
    project_name: str,
    description: str | None,
    files: list[tuple[str, bytes]],
) -> bytes:
    """
    Build a .ragpack.zip with manifest.json + document files.

    ``files`` is a list of (archive_path, content_bytes).
    """
    documents = [
        {"type": "file", "path": name, "title": Path(name).stem} for name, _ in files
    ]
    manifest = {
        "version": "1.0",
        "projects": [
            {
                "name": project_name,
                "description": description,
                "documents": documents,
            }
        ],
    }
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for name, content in files:
            zf.writestr(name, content)
    return buf.getvalue()
