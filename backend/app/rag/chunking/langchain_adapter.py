"""
Shared LangChain adapters for FlexSearch chunking.

Converts LangChain ``Document`` outputs into FlexSearch ``Chunk`` objects and
bridges ``EmbeddingService`` to LangChain's ``Embeddings`` ABC for semantic
splitting.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from app.rag.chunking.base import Chunk

# Fenced code or markdown pipe tables — kept atomic when preserve_structure=True
STRUCTURE_RE = re.compile(
    r"(```[\s\S]*?```)"
    r"|"
    r"(?:(?:^|\n)(?:\|[^\n]+\|\n)+\|[-:| ]+\|\n(?:\|[^\n]+\|\n?)+)",
    re.MULTILINE,
)


class FlexSearchEmbeddings(Embeddings):
    """LangChain Embeddings backed by FlexSearch ``EmbeddingService``."""

    def __init__(self, embedding_service: Any | None = None) -> None:
        self._service = embedding_service

    def _svc(self) -> Any:
        if self._service is None:
            from app.services.embedding import get_embedding_service

            self._service = get_embedding_service()
        return self._service

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._svc().embed_batch(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._svc().embed(text)


def documents_to_chunks(
    documents: Iterable[Document],
    *,
    source_text: str,
    document_id: str,
    metadata: dict[str, Any] | None = None,
    extra_meta: dict[str, Any] | None = None,
    start_index: int = 0,
) -> list[Chunk]:
    """
    Map LangChain Documents to FlexSearch Chunks with stable char offsets.

    Prefers ``metadata['start_index']`` from LangChain splitters
    (``add_start_index=True``). Falls back to ``str.find`` from a running cursor.
    """
    chunks: list[Chunk] = []
    cursor = 0
    base_meta = dict(metadata or {})
    for idx, doc in enumerate(documents):
        content = (doc.page_content or "").strip()
        if not content:
            continue

        doc_meta = dict(doc.metadata or {})
        raw_start = doc_meta.pop("start_index", None)
        if isinstance(raw_start, int) and raw_start >= 0:
            start = start_index + raw_start
            # After strip_whitespace, content may be shorter than the window —
            # locate the stripped content near the reported start.
            found = source_text.find(content, max(0, start - 8))
            if found != -1 and abs(found - start) <= len(content) + 16:
                start = found
        else:
            found = source_text.find(content, cursor)
            start = found if found != -1 else cursor

        end = start + len(content)
        meta = {**base_meta, **doc_meta, **(extra_meta or {})}
        chunks.append(
            Chunk(
                content=content,
                document_id=document_id,
                chunk_index=idx,
                start_char=start,
                end_char=end,
                metadata=meta,
            )
        )
        cursor = max(cursor, start + 1)

    # Re-number in case empties were skipped
    for i, chunk in enumerate(chunks):
        chunk.chunk_index = i
    return chunks


def split_preserving_structure(text: str) -> list[tuple[str, str | None]]:
    """
    Split text into (segment, structure_type) pairs.

    ``structure_type`` is ``\"code\"`` / ``\"table\"`` for atomic units, else
    ``None`` for prose that should go through a recursive splitter.
    """
    parts: list[tuple[str, str | None]] = []
    last = 0
    for match in STRUCTURE_RE.finditer(text):
        if match.start() > last:
            prose = text[last : match.start()]
            if prose.strip():
                parts.append((prose, None))
        atom = match.group(0)
        if atom and atom.strip():
            cleaned = atom.strip("\n")
            kind = "code" if cleaned.lstrip().startswith("```") else "table"
            parts.append((cleaned, kind))
        last = match.end()
    if last < len(text) and text[last:].strip():
        parts.append((text[last:], None))
    return parts


def resolve_end_char(source_text: str, start: int, content: str) -> int:
    """Best-effort end offset for a chunk content slice."""
    if start < 0:
        start = 0
    end = start + len(content)
    if source_text[start:end] == content:
        return end
    found = source_text.find(content, start)
    if found != -1:
        return found + len(content)
    return min(len(source_text), end)
