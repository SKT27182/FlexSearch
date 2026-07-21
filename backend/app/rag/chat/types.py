"""Chat orchestrator types and citation helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass
class Citation:
    index: int
    chunk_id: str
    document_id: str
    content: str
    score: float
    filename: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "content": self.content,
            "score": self.score,
            "filename": self.filename,
            "metadata": self.metadata,
        }


@dataclass
class ChatTurnMemory:
    role: str
    content: str


@dataclass
class ChatAnswer:
    answer: str
    citations: list[Citation]
    retrieval_strategy: str
    reranking_strategy: str
    session_id: str | None = None
    turn_id: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    empty_retrieval: bool = False
    grounded: bool = False
    invalid_citations: list[int] = field(default_factory=list)
    debug: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "answer": self.answer,
            "citations": [c.to_dict() for c in self.citations],
            "retrieval_strategy": self.retrieval_strategy,
            "reranking_strategy": self.reranking_strategy,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "empty_retrieval": self.empty_retrieval,
            "grounded": self.grounded,
            "invalid_citations": self.invalid_citations,
        }
        if self.debug is not None:
            payload["debug"] = self.debug
        return payload


def build_citations(results: list[Any]) -> list[Citation]:
    """Build citations, expanding summary hits to member chunks when needed."""
    from app.rag.retrieval.hierarchy import expand_summary_hits

    # Expand any remaining summary-level hits so chat cites concrete chunks
    expanded = expand_summary_hits(
        list(results),
        keep_summaries=False,
    )
    citations: list[Citation] = []
    for i, result in enumerate(expanded, start=1):
        meta = getattr(result, "metadata", None) or {}
        filename = meta.get("filename") if isinstance(meta, dict) else None
        citations.append(
            Citation(
                index=i,
                chunk_id=result.chunk_id,
                document_id=result.document_id,
                content=result.content,
                score=float(result.score),
                filename=filename,
                metadata=dict(meta) if isinstance(meta, dict) else {},
            )
        )
    return citations


def format_passages(citations: list[Citation]) -> list[dict[str, Any]]:
    return [
        {
            "index": c.index,
            "content": c.content,
            "filename": c.filename,
            "document_id": c.document_id,
            "chunk_id": c.chunk_id,
            "score": c.score,
        }
        for c in citations
    ]


_CITATION_MARKER = re.compile(r"\[(\d+)\]")


def validate_answer_citations(
    answer: str, citations: list[Citation]
) -> tuple[str, list[int], bool]:
    """Remove citation markers not present in the supplied retrieval context."""
    valid = {citation.index for citation in citations}
    invalid: list[int] = []
    used_valid = False

    def replace(match: re.Match[str]) -> str:
        nonlocal used_valid
        index = int(match.group(1))
        if index in valid:
            used_valid = True
            return match.group(0)
        invalid.append(index)
        return ""

    cleaned = _CITATION_MARKER.sub(replace, answer)
    return cleaned, sorted(set(invalid)), bool(citations) and used_valid
