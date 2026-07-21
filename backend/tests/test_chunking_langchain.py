"""LangChain-backed chunking strategies — unit tests."""

from __future__ import annotations

from unittest.mock import patch

from app.rag.chunking import (
    FixedWindowChunking,
    ParentChildChunking,
    RecursiveChunking,
    SemanticChunking,
)
from app.rag.factory import build_chunking_strategy
from app.schemas.rag_config import ChunkingConfig


class _FakeEmbeddingService:
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i, _ in enumerate(texts):
            v = [0.0] * 8
            # Two topic clusters so SemanticChunker finds breakpoints
            if i % 4 < 2:
                v[0] = 1.0
            else:
                v[1] = 1.0
            out.append(v)
        return out

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]


def test_fixed_window_uses_langchain_offsets() -> None:
    text = "word " * 200
    chunks = FixedWindowChunking(chunk_size=64, overlap=8).chunk(text, "doc-fw")
    assert len(chunks) >= 2
    assert chunks[0].start_char == 0
    assert chunks[0].document_id == "doc-fw"
    # Metadata dicts must be independent
    chunks[0].metadata["x"] = 1
    assert "x" not in chunks[1].metadata


def test_recursive_preserve_structure_tags_code() -> None:
    text = "Prose before.\n\n```python\ndef hello():\n    return 1\n```\n\nProse after."
    chunks = RecursiveChunking(chunk_size=40, overlap=0, preserve_structure=True).chunk(
        text, "doc-rc"
    )
    joined = "\n".join(c.content for c in chunks)
    assert "```python" in joined
    assert "def hello" in joined
    assert any(c.metadata.get("structure_type") == "code" for c in chunks)


def test_recursive_without_structure_still_chunks() -> None:
    text = "# Title\n\n" + ("paragraph text. " * 40)
    chunks = RecursiveChunking(
        chunk_size=80, overlap=10, preserve_structure=False
    ).chunk(text, "doc-rc2")
    assert len(chunks) >= 2
    assert all(c.content.strip() for c in chunks)


def test_parent_child_id_contract() -> None:
    text = ("Parent context sentence. " * 80) + ("\n\nChild detail. " * 40)
    chunks = ParentChildChunking(
        parent_chunk_size=400, child_chunk_size=80, overlap=10
    ).chunk(text, "doc-pc")
    parents = [c for c in chunks if c.metadata.get("chunk_type") == "parent"]
    children = [c for c in chunks if c.metadata.get("chunk_type") == "child"]
    assert parents
    assert children
    parent_ids = {p.metadata["parent_chunk_id"] for p in parents}
    assert all(c.parent_id in parent_ids for c in children)
    assert all(p.parent_id is None for p in parents)


def test_semantic_langchain_chunker_with_fake_embeddings() -> None:
    text = (
        "Alpha topic one. Alpha continues here. "
        "Beta different subject. Beta more words. "
        "Gamma another idea. Gamma wrap up."
    )
    with patch(
        "app.services.embedding.get_embedding_service",
        return_value=_FakeEmbeddingService(),
    ):
        chunks = SemanticChunking(
            similarity_threshold=0.5,
            min_chunk_size=10,
            max_chunk_size=200,
        ).chunk(text, "doc-sem")
    assert len(chunks) >= 1
    assert chunks[0].start_char >= 0
    assert all(c.document_id == "doc-sem" for c in chunks)


def test_semantic_caps_oversized_groups() -> None:
    # One long run of similar embeddings → SemanticChunker may emit a large group;
    # max_chunk_size must still split via RecursiveCharacterTextSplitter.
    long = ("Same idea continues. " * 80).strip()
    with patch(
        "app.services.embedding.get_embedding_service",
        return_value=_FakeEmbeddingService(),
    ):
        chunks = SemanticChunking(
            similarity_threshold=0.95,
            min_chunk_size=20,
            max_chunk_size=120,
        ).chunk(long, "doc-cap")
    assert chunks
    assert all(len(c.content) <= 130 for c in chunks)


def test_factory_builds_all_langchain_strategies() -> None:
    assert (
        build_chunking_strategy(
            ChunkingConfig(strategy="fixed_window", params={"chunk_size": 128})
        ).name
        == "fixed_window"
    )
    assert (
        build_chunking_strategy(
            ChunkingConfig(strategy="recursive", params={"preserve_structure": True})
        ).name
        == "recursive"
    )
    assert (
        build_chunking_strategy(
            ChunkingConfig(
                strategy="semantic",
                params={
                    "similarity_threshold": 0.4,
                    "breakpoint_threshold_type": "percentile",
                    "buffer_size": 1,
                },
            )
        ).name
        == "semantic"
    )
    assert (
        build_chunking_strategy(
            ChunkingConfig(
                strategy="parent_child",
                params={"parent_chunk_size": 800, "child_chunk_size": 200},
            )
        ).name
        == "parent_child"
    )
