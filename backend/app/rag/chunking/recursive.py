"""
Recursive chunking via LangChain ``RecursiveCharacterTextSplitter``.

When ``preserve_structure=True``, fenced code blocks and markdown pipe tables
are extracted as atomic units (never mid-split) before prose is handed to the
LangChain recursive splitter — same contract as the previous custom
implementation, backed by LangChain for the scalable recursive path.
"""

from __future__ import annotations

from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.rag.chunking.langchain_adapter import (
    documents_to_chunks,
    split_preserving_structure,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)

# Prefer larger structural breaks first (LangChain defaults + FlexSearch extras)
DEFAULT_SEPARATORS = [
    "\n\n\n",
    "\n\n",
    "\n",
    ". ",
    ", ",
    " ",
    "",
]


class RecursiveChunking(BaseChunkingStrategy):
    """Recursive text splitting with hierarchical separators (LangChain)."""

    def __init__(
        self,
        chunk_size: int = 512,
        overlap: int = 50,
        separators: list[str] | None = None,
        *,
        preserve_structure: bool = True,
    ) -> None:
        self._chunk_size = chunk_size
        self._overlap = overlap
        self._separators = separators or DEFAULT_SEPARATORS
        self._preserve_structure = preserve_structure
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            separators=self._separators,
            is_separator_regex=False,
            add_start_index=True,
            strip_whitespace=True,
            keep_separator=True,
        )

    @property
    def name(self) -> str:
        return "recursive"

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        if not text.strip():
            return []

        if self._preserve_structure:
            chunks = self._chunk_with_structure(text, document_id, metadata)
        else:
            docs = self._splitter.create_documents([text])
            chunks = documents_to_chunks(
                docs,
                source_text=text,
                document_id=document_id,
                metadata=metadata,
            )

        logger.debug(
            "Created %d recursive chunks from document %s",
            len(chunks),
            document_id,
        )
        return chunks

    def _chunk_with_structure(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None,
    ) -> list[Chunk]:
        """Prose → RecursiveCharacterTextSplitter; code/tables stay atomic."""
        result: list[Chunk] = []
        cursor = 0
        for segment, structure_type in split_preserving_structure(text):
            if structure_type is not None:
                content = segment.strip()
                if not content:
                    continue
                start = text.find(content, cursor)
                if start == -1:
                    start = cursor
                end = start + len(content)
                meta = dict(metadata or {})
                meta["structure_type"] = structure_type
                # Oversized atoms: fall back to hard recursive split still via LC
                if len(content) > self._chunk_size * 2:
                    docs = self._splitter.create_documents([content])
                    sub = documents_to_chunks(
                        docs,
                        source_text=content,
                        document_id=document_id,
                        metadata=metadata,
                        extra_meta={"structure_type": structure_type},
                    )
                    for sub_chunk in sub:
                        sub_chunk.start_char += start
                        sub_chunk.end_char += start
                        result.append(sub_chunk)
                else:
                    result.append(
                        Chunk(
                            content=content,
                            document_id=document_id,
                            chunk_index=0,
                            start_char=start,
                            end_char=end,
                            metadata=meta,
                        )
                    )
                cursor = max(cursor, start + 1)
                continue

            # Prose segment — relative start_index from LC must be offset
            abs_offset = text.find(segment, cursor)
            if abs_offset == -1:
                abs_offset = cursor
            docs = self._splitter.create_documents([segment])
            for doc in docs:
                if "start_index" in (doc.metadata or {}):
                    doc.metadata["start_index"] = (
                        abs_offset + int(doc.metadata["start_index"])
                    )
            prose_chunks = documents_to_chunks(
                docs,
                source_text=text,
                document_id=document_id,
                metadata=metadata,
            )
            result.extend(prose_chunks)
            if prose_chunks:
                cursor = max(cursor, prose_chunks[-1].end_char)
            else:
                cursor = abs_offset + len(segment)

        for i, chunk in enumerate(result):
            chunk.chunk_index = i
        return result
