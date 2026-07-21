"""Focused regression tests for breaking-release security and integrity contracts."""

import math
import re
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.rag.chat.types import Citation, validate_answer_citations
from app.rag.chunking.base import Chunk
from app.rag.pipeline import RAGPipeline
from app.db.models import RagMode
from app.services.upload_validation import validate_supported_upload
from app.services.safe_http import ValidatedResolver
from app.services.url_safety import UnsafeURLError


def test_env_example_catalog_matches_settings_model() -> None:
    from app.core.config import Settings

    catalog = Path(__file__).parents[1] / ".env.example"
    keys: set[str] = set()
    for line in catalog.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=", line)
        if match:
            keys.add(match.group(1))

    expected = {name.upper() for name in Settings.model_fields}
    assert expected == keys
    assert {"DATABASE_URL", "JWT_SECRET_KEY", "LLM_API_KEY"}.isdisjoint(keys)


@pytest.mark.asyncio
async def test_dns_pin_rejects_mixed_public_private_answers(monkeypatch) -> None:
    class FakeLoop:
        async def getaddrinfo(self, *_args, **_kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
            ]

    monkeypatch.setattr("asyncio.get_running_loop", lambda: FakeLoop())
    with pytest.raises(UnsafeURLError):
        await ValidatedResolver().resolve("attacker.example", 80)


@pytest.mark.parametrize(
    "filename,mime,prefix,expected",
    [
        ("a.pdf", "application/pdf", b"%PDF-1.7\n", "application/pdf"),
        ("a.png", "image/png", b"\x89PNG\r\n\x1a\n", "image/png"),
        ("a.jpg", "image/jpeg", b"\xff\xd8\xff\xe0", "image/jpeg"),
        ("a.txt", "text/plain", b"valid utf-8", "text/plain"),
    ],
)
def test_supported_upload_registry(filename, mime, prefix, expected) -> None:
    assert (
        validate_supported_upload(
            filename=filename, declared_content_type=mime, prefix=prefix
        )
        == expected
    )


@pytest.mark.parametrize(
    "filename,mime,prefix",
    [
        ("payload.pdf", "application/pdf", b"MZ executable"),
        ("payload.exe", "application/pdf", b"%PDF-1.7"),
        ("sheet.csv", "text/csv", b"a,b\n1,2"),
        ("deck.pptx", "application/zip", b"PK\x03\x04"),
        ("empty.txt", "text/plain", b""),
    ],
)
def test_spoofed_and_unsupported_uploads_are_rejected(filename, mime, prefix) -> None:
    with pytest.raises(HTTPException) as exc:
        validate_supported_upload(
            filename=filename, declared_content_type=mime, prefix=prefix
        )
    assert exc.value.status_code == 400


def test_unknown_citations_are_removed_and_reported() -> None:
    citations = [Citation(1, "chunk", "doc", "evidence", 0.9)]
    answer, invalid, grounded = validate_answer_citations(
        "Supported [1], invented [9].", citations
    )
    assert "[1]" in answer
    assert "[9]" not in answer
    assert invalid == [9]
    assert grounded is True


@pytest.mark.parametrize(
    "vectors",
    [[], [[1.0, 2.0], [3.0, 4.0]], [[math.nan, 1.0]], [[1.0]], [[]]],
)
def test_embedding_batch_is_validated_before_index_write(vectors) -> None:
    pipeline = object.__new__(RAGPipeline)
    pipeline._rag_mode = RagMode.VECTOR
    pipeline._embedding = SimpleNamespace(
        dimension=2, embed_batch=lambda _texts: vectors
    )
    store = SimpleNamespace(upsert=lambda _documents: pytest.fail("write occurred"))
    pipeline._search_store = store
    chunks = [Chunk("text", "doc", 0, 0, 4)]
    with pytest.raises(ValueError):
        pipeline.index_chunks(chunks, "doc", "project", "file.txt")
