"""Phase 3: preprocess, hierarchy, structure chunking, hierarchical retrieval."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.rag.chunking.recursive import RecursiveChunking
from app.rag.chat.types import build_citations
from app.rag.factory import build_extraction_strategy, build_chunking_strategy
from app.rag.ingestion.hierarchy import annotate_chunks_with_hierarchy, extract_heading_spans
from app.rag.ingestion.preprocess import (
    normalize_whitespace,
    preprocess_extracted_text,
    remove_repeated_headers_footers,
)
from app.rag.retrieval.base import RetrievalResult
from app.rag.retrieval.hierarchy import (
    apply_hierarchy_postprocess,
    expand_summary_hits,
    filters_for_hierarchy,
    summary_levels_for_mode,
)
from app.schemas.rag_config import (
    ChunkingConfig,
    ExtractionConfig,
    HierarchicalSummaryConfig,
    VectorRagConfig,
)
from app.services.search_store.types import SearchHit


def test_preprocess_normalizes_whitespace() -> None:
    text = "Hello   \n\n\n\nWorld  \n"
    assert normalize_whitespace(text) == "Hello\n\nWorld"


def test_preprocess_strips_repeated_headers() -> None:
    pages = ["CONFIDENTIAL\nBody A\nPage 1", "CONFIDENTIAL\nBody B\nPage 2", "CONFIDENTIAL\nBody C\nPage 3"]
    text = "\n\n".join(pages)
    cleaned = remove_repeated_headers_footers(text, min_occurrences=3)
    assert "CONFIDENTIAL" not in cleaned
    assert "Body A" in cleaned


def test_preprocess_pipeline_runs() -> None:
    out = preprocess_extracted_text("caf\u00e9\n\n\n\nmore")
    assert "café" in out or "cafe" in out.lower() or "caf" in out
    assert "\n\n\n" not in out


def test_factory_builds_docling_and_hybrid() -> None:
    assert build_extraction_strategy(ExtractionConfig(strategy="docling")).name == "docling"
    assert build_extraction_strategy(ExtractionConfig(strategy="hybrid_pdf")).name == "hybrid_pdf"
    assert build_extraction_strategy(ExtractionConfig(strategy="ocr")).name == "ocr"


def test_heading_spans_and_chunk_annotation() -> None:
    text = "# Intro\n\nHello\n\n## Details\n\nMore text here"
    spans = extract_heading_spans(text)
    assert len(spans) == 2
    assert spans[0].path == ["Intro"]
    assert spans[1].path == ["Intro", "Details"]

    chunker = RecursiveChunking(chunk_size=80, overlap=0, preserve_structure=False)
    chunks = chunker.chunk(text, "doc-1")
    annotate_chunks_with_hierarchy(text, chunks)
    assert any(c.metadata.get("heading_path") for c in chunks)


def test_fixed_window_hierarchy_paths_are_per_chunk() -> None:
    """Default chunker must not share metadata dicts across chunks."""
    from app.rag.chunking.fixed_window import FixedWindowChunking

    intro = "hello world " * 80
    details = "more text " * 80
    text = f"# Intro\n\n{intro}\n\n## Details\n\n{details}"
    chunks = FixedWindowChunking(chunk_size=120, overlap=0).chunk(text, "doc-1")
    assert len(chunks) >= 2
    annotate_chunks_with_hierarchy(text, chunks)
    paths = [tuple(c.metadata.get("heading_path") or []) for c in chunks]
    assert len(set(paths)) >= 2, f"expected distinct heading paths, got {paths}"


def test_recursive_preserves_code_fence() -> None:
    text = (
        "Prose before.\n\n"
        "```python\ndef hello():\n    return 1\n```\n\n"
        "Prose after."
    )
    chunks = RecursiveChunking(chunk_size=40, overlap=0, preserve_structure=True).chunk(
        text, "doc-1"
    )
    joined = "\n".join(c.content for c in chunks)
    assert "```python" in joined
    assert "def hello" in joined
    # Code should appear as a single structure-tagged chunk when small enough
    code_chunks = [c for c in chunks if c.metadata.get("structure_type") == "code"]
    assert code_chunks


def test_recursive_preserve_structure_param_from_config() -> None:
    cfg = ChunkingConfig(
        strategy="recursive",
        params={"chunk_size": 256, "overlap": 20, "preserve_structure": True},
    )
    strategy = build_chunking_strategy(cfg)
    assert strategy.name == "recursive"
    assert getattr(strategy, "_preserve_structure") is True


def test_summary_levels_for_mode() -> None:
    assert summary_levels_for_mode("chunks_only") == ["chunk"]
    assert summary_levels_for_mode("summaries_first") == ["cluster", "document"]
    assert summary_levels_for_mode("mixed") is None


def test_filters_for_hierarchy_modes() -> None:
    f1 = filters_for_hierarchy("p1", "chunks_only")
    assert f1.summary_level == "chunk"
    f2 = filters_for_hierarchy("p1", "summaries_first")
    assert f2.summary_levels == ["cluster", "document"]
    f3 = filters_for_hierarchy("p1", "mixed")
    assert f3.summary_level is None
    assert f3.summary_levels is None


def test_expand_summary_hits_replaces_with_members() -> None:
    member = SearchHit(
        id="c1",
        score=0.5,
        content="member text",
        document_id="d1",
        summary_level="chunk",
        filename="a.md",
    )
    store = MagicMock()
    store.get_by_ids.return_value = [member]

    summary = RetrievalResult(
        content="cluster summary",
        score=0.9,
        document_id="d1",
        chunk_id="s1",
        metadata={
            "summary_level": "cluster",
            "member_chunk_ids": ["c1"],
            "filename": "a.md",
        },
    )
    with patch("app.rag.retrieval.hierarchy.get_search_store", return_value=store):
        expanded = expand_summary_hits([summary], keep_summaries=False)

    assert len(expanded) == 1
    assert expanded[0].chunk_id == "c1"
    assert expanded[0].content == "member text"
    assert expanded[0].metadata.get("expanded_from_summary") == "s1"


def test_mixed_mode_keeps_summary_and_members() -> None:
    member = SearchHit(
        id="c1",
        score=0.4,
        content="member",
        document_id="d1",
        summary_level="chunk",
    )
    store = MagicMock()
    store.get_by_ids.return_value = [member]
    summary = RetrievalResult(
        content="sum",
        score=0.8,
        document_id="d1",
        chunk_id="s1",
        metadata={"summary_level": "document", "member_chunk_ids": ["c1"]},
    )
    with patch("app.rag.retrieval.hierarchy.get_search_store", return_value=store):
        out = apply_hierarchy_postprocess([summary], "mixed")
    assert {r.chunk_id for r in out} == {"s1", "c1"}


def test_chat_citations_expand_summaries() -> None:
    member = SearchHit(
        id="chunk-a",
        score=0.3,
        content="cited chunk",
        document_id="d1",
        summary_level="chunk",
        filename="doc.pdf",
    )
    store = MagicMock()
    store.get_by_ids.return_value = [member]
    results = [
        RetrievalResult(
            content="summary",
            score=0.99,
            document_id="d1",
            chunk_id="sum-1",
            metadata={
                "summary_level": "cluster",
                "member_chunk_ids": ["chunk-a"],
                "filename": "doc.pdf",
            },
        )
    ]
    with patch("app.rag.retrieval.hierarchy.get_search_store", return_value=store):
        citations = build_citations(results)
    assert len(citations) == 1
    assert citations[0].chunk_id == "chunk-a"
    assert citations[0].content == "cited chunk"


def test_vector_config_includes_summaries() -> None:
    cfg = VectorRagConfig()
    assert cfg.summaries.enabled is True
    assert cfg.summaries.retrieval_mode == "chunks_only"
    assert isinstance(cfg.summaries, HierarchicalSummaryConfig)


def test_summary_skip_reason_helpers() -> None:
    from app.services.summary.service import SummaryJobResult, summary_meta_payload

    meta = summary_meta_payload(
        SummaryJobResult(
            document_id="d",
            project_id="p",
            cluster_count=0,
            manifesto_id=None,
            skipped=True,
            reason="microsoft_graphrag",
        )
    )
    assert meta["skipped"] is True
    assert meta["reason"] == "microsoft_graphrag"


@pytest.mark.asyncio
async def test_hybrid_pdf_extracts_plain_text() -> None:
    from app.rag.ingestion.hybrid_pdf import HybridPdfExtractionStrategy

    strategy = HybridPdfExtractionStrategy()
    result = await strategy.extract(b"hello world", "text/plain", "a.txt")
    assert "hello world" in result.text
    assert result.metadata["strategy"] == "hybrid_pdf"
