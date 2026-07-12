"""
Semantic chunking via LangChain ``SemanticChunker``.

Uses FlexSearch ``EmbeddingService`` through ``FlexSearchEmbeddings`` so local
sentence-transformers and LiteLLM API embeddings both work. Oversized semantic
groups are capped with ``RecursiveCharacterTextSplitter`` for production
context-window safety.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_experimental.text_splitter import SemanticChunker
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.rag.chunking.langchain_adapter import (
    FlexSearchEmbeddings,
    documents_to_chunks,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)

BreakpointType = Literal[
    "percentile",
    "standard_deviation",
    "interquartile",
    "gradient",
]


class SemanticChunking(BaseChunkingStrategy):
    """Embedding-based semantic splitting (LangChain SemanticChunker)."""

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

        # Map legacy similarity_threshold (higher = fewer breaks) onto SemanticChunker's
        # distance breakpoint amount. For percentile: amount ≈ (1 - sim) * 100.
        amount = max(1.0, min(99.0, (1.0 - similarity_threshold) * 100.0))
        self._embeddings = FlexSearchEmbeddings()
        self._splitter = SemanticChunker(
            embeddings=self._embeddings,
            buffer_size=buffer_size,
            add_start_index=True,
            breakpoint_threshold_type=breakpoint_threshold_type,
            breakpoint_threshold_amount=amount,
            min_chunk_size=min_chunk_size,
        )
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
        if not text.strip():
            return []

        docs = self._splitter.create_documents([text])
        # Cap oversized semantic groups for embedding / context limits
        capped: list = []
        for doc in docs:
            content = doc.page_content or ""
            base_start = int(doc.metadata.get("start_index", 0) or 0)
            if len(content) <= self._max_chunk_size:
                capped.append(doc)
                continue
            sub_docs = self._cap_splitter.create_documents([content])
            for sub in sub_docs:
                rel = int(sub.metadata.get("start_index", 0) or 0)
                sub.metadata["start_index"] = base_start + rel
                capped.append(sub)

        chunks = documents_to_chunks(
            capped,
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
