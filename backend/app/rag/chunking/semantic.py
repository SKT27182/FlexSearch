"""Embedding-based semantic chunking without deprecated experimental packages."""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.rag.chunking.langchain_adapter import documents_to_chunks
from app.utils.logger import create_logger

logger = create_logger(__name__)

BreakpointType = Literal[
    "percentile",
    "standard_deviation",
    "interquartile",
    "gradient",
]
_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n+|$)", re.MULTILINE)


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


class SemanticChunking(BaseChunkingStrategy):
    """Split at low-similarity sentence boundaries, then cap oversized groups."""

    def __init__(
        self,
        similarity_threshold: float = 0.5,
        min_chunk_size: int = 100,
        max_chunk_size: int = 1000,
        *,
        breakpoint_threshold_type: BreakpointType = "percentile",
        buffer_size: int = 1,
    ) -> None:
        self._similarity_threshold = similarity_threshold
        self._min_chunk_size = min_chunk_size
        self._max_chunk_size = max_chunk_size
        self._breakpoint_threshold_type = breakpoint_threshold_type
        self._buffer_size = buffer_size
        self._cap_splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_size,
            chunk_overlap=min(50, max(0, max_chunk_size // 10)),
            length_function=len,
            add_start_index=True,
            strip_whitespace=True,
        )

    @property
    def name(self) -> str:
        return "semantic"

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        spans = [
            match for match in _SENTENCE_RE.finditer(text) if match.group().strip()
        ]
        if not spans:
            return []

        contexts: list[str] = []
        for index in range(len(spans)):
            start = max(0, index - self._buffer_size)
            end = min(len(spans), index + self._buffer_size + 1)
            contexts.append(
                " ".join(spans[i].group().strip() for i in range(start, end))
            )
        from app.services.embedding import get_embedding_service

        vectors = get_embedding_service().embed_batch(contexts)
        if len(vectors) != len(spans):
            raise ValueError(
                f"Embedding count mismatch: expected {len(spans)}, got {len(vectors)}"
            )

        groups: list[tuple[int, int]] = []
        group_start = spans[0].start()
        for index in range(len(spans) - 1):
            similarity = _cosine(vectors[index], vectors[index + 1])
            boundary = spans[index].end()
            if (
                similarity < self._similarity_threshold
                and boundary - group_start >= self._min_chunk_size
            ):
                groups.append((group_start, boundary))
                group_start = spans[index + 1].start()
        groups.append((group_start, spans[-1].end()))

        documents: list[Document] = []
        for start, end in groups:
            content = text[start:end].strip()
            if not content:
                continue
            actual_start = text.find(content, start, end)
            if len(content) <= self._max_chunk_size:
                documents.append(
                    Document(
                        page_content=content,
                        metadata={"start_index": actual_start},
                    )
                )
                continue
            for sub_document in self._cap_splitter.create_documents([content]):
                relative = int(sub_document.metadata.get("start_index", 0) or 0)
                sub_document.metadata["start_index"] = actual_start + relative
                documents.append(sub_document)

        chunks = documents_to_chunks(
            documents,
            source_text=text,
            document_id=document_id,
            metadata=metadata,
        )
        logger.debug(
            "Created %d semantic chunks from document %s",
            len(chunks),
            document_id,
        )
        return chunks
