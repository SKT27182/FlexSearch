"""Phase 4 unit tests: crawler helpers, ragpack, suggestions parsing."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.services.bulk.ragpack import (
    build_ragpack_zip,
    find_manifest_dir,
    load_manifest,
    safe_extract_zip,
    safe_join,
    validate_referenced_files,
)
from app.services.bulk.schemas import BulkImportManifest
from app.services.suggestion.service import _parse_questions
from app.services.website.content_extractor import extract_clean_content
from app.services.website.crawler import (
    _is_crawlable_url,
    _matches_exclude_pattern,
    normalise_url,
)


def test_normalise_url_strips_fragment_and_slash():
    assert (
        normalise_url("https://example.com/docs/#section") == "https://example.com/docs"
    )
    assert normalise_url("https://example.com/") == "https://example.com/"


def test_crawlable_and_exclude():
    assert _is_crawlable_url("https://example.com/page")
    assert not _is_crawlable_url("https://example.com/logo.png")
    assert _matches_exclude_pattern("https://example.com/admin/x", ["/admin/*"])
    assert not _matches_exclude_pattern("https://example.com/docs", ["/admin/*"])


def test_extract_clean_content_fallback():
    html = """
    <html><head><title>T</title></head>
    <body>
      <nav>Nav</nav>
      <main>
        <h1>Hello</h1>
        <p>World content here.</p>
      </main>
      <footer>Footer</footer>
    </body></html>
    """
    text = extract_clean_content(html)
    assert "Hello" in text
    assert "World" in text


def test_ragpack_roundtrip(tmp_path: Path):
    zip_bytes = build_ragpack_zip(
        project_name="Demo",
        description="test",
        files=[("documents/a.md", b"# Hello\n\nBody")],
    )
    zpath = tmp_path / "demo.ragpack.zip"
    zpath.write_bytes(zip_bytes)

    extract = tmp_path / "out"
    extract.mkdir()
    with zipfile.ZipFile(zpath, "r") as zf:
        safe_extract_zip(zf, extract)

    base = find_manifest_dir(extract)
    manifest = load_manifest(base)
    assert isinstance(manifest, BulkImportManifest)
    assert manifest.projects[0].name == "Demo"
    validate_referenced_files(manifest, base)


def test_safe_extract_rejects_zip_slip(tmp_path: Path):
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("manifest.json", '{"version":"1.0","projects":[]}')
        zf.writestr("../outside.txt", "pwned")

    extract = tmp_path / "out"
    extract.mkdir()
    with zipfile.ZipFile(evil, "r") as zf:
        with pytest.raises(ValueError, match="escapes|Invalid"):
            safe_extract_zip(zf, extract)
    assert not (tmp_path / "outside.txt").exists()


def test_safe_join_rejects_traversal(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_join(tmp_path, "../../etc/passwd")
    nested = safe_join(tmp_path, "documents/a.md")
    assert nested == (tmp_path / "documents" / "a.md").resolve()


def test_parse_questions_json_and_fallback():
    assert _parse_questions('{"questions": ["A?", "B?"]}', limit=2) == ["A?", "B?"]
    assert _parse_questions('["One?", "Two?"]', limit=1) == ["One?"]
    lines = _parse_questions("1. What is X?\n2. How does Y?", limit=5)
    assert len(lines) == 2
    assert lines[0].endswith("?")
