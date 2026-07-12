"""
Fixed-window chunking via LangChain ``CharacterTextSplitter``.

Production notes:
- Uses ``add_start_index=True`` for stable char offsets into OpenSearch metadata.
- Breaks on whitespace (``separator=\" \"``) to avoid mid-word cuts.
- Stateless splitter instance is cheap to construct per strategy config.
"""

from __future__ import annotations

from typing import Any

from langchain_text_splitters import CharacterTextSplitter

from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.rag.chunking.langchain_adapter import documents_to_chunks
from app.utils.logger import create_logger

logger = create_logger(__name__)


class FixedWindowChunking(BaseChunkingStrategy):
    """Fixed-size window chunking with overlap (LangChain CharacterTextSplitter)."""

    def __init__(
        self,
        chunk_size: int = 512,
        overlap: int = 50,
    ) -> None:
        self._chunk_size = chunk_size
        self._overlap = overlap
        # keep_separator=False: CharacterTextSplitter strips the separator from
        # joins; space-separated windows match historical FlexSearch behavior.
        self._splitter = CharacterTextSplitter(
            separator=" ",
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            is_separator_regex=False,
            add_start_index=True,
            strip_whitespace=True,
        )

    @property
    def name(self) -> str:
        return "fixed_window"

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        if not text.strip():
            return []

        docs = self._splitter.create_documents([text])
        chunks = documents_to_chunks(
            docs,
            source_text=text,
            document_id=document_id,
            metadata=metadata,
        )
        logger.debug(
            "Created %d fixed_window chunks from document %s",
            len(chunks),
            document_id,
        )
        return chunks
