"""Parse / build .ragpack zip archives."""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

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
    """Extract zip members only if each stays under ``extract_dir`` (zip-slip safe)."""
    root = Path(extract_dir).resolve()
    for info in zf.infolist():
        name = info.filename
        if not name or name.endswith("/"):
            # Directory entries — still validate destination.
            dest = safe_join(root, name.rstrip("/") or ".")
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest = safe_join(root, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info, "r") as src, open(dest, "wb") as out:
            out.write(src.read())


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
        {"type": "file", "path": name, "title": Path(name).stem}
        for name, _ in files
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
