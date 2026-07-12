"""
Parent-child hierarchical chunking via LangChain recursive splitters.

Parents provide retrieval context; children are the dense-search units.
Parent/child ID contract is unchanged for OpenSearch + ParentChildRetrieval:
- parent metadata: ``is_parent``, ``chunk_type=parent``, ``parent_chunk_id``
- child metadata: ``chunk_type=child``, ``parent_id`` → parent_chunk_id
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.rag.chunking.base import BaseChunkingStrategy, Chunk
from app.rag.chunking.langchain_adapter import documents_to_chunks
from app.utils.logger import create_logger

logger = create_logger(__name__)

_PARENT_SEPARATORS = ["\n\n\n", "\n\n", "\n", ". ", " ", ""]
_CHILD_SEPARATORS = ["\n\n", "\n", ". ", " ", ""]


class ParentChildChunking(BaseChunkingStrategy):
    """Parent-child hierarchical chunking (LangChain RecursiveCharacterTextSplitter)."""

    def __init__(
        self,
        parent_chunk_size: int = 1500,
        child_chunk_size: int = 300,
        overlap: int = 50,
    ) -> None:
        self._parent_size = parent_chunk_size
        self._child_size = child_chunk_size
        self._overlap = overlap
        # Parents abut (no overlap) so child offsets stay within one parent window.
        self._parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=parent_chunk_size,
            chunk_overlap=0,
            length_function=len,
            separators=_PARENT_SEPARATORS,
            add_start_index=True,
            strip_whitespace=True,
            keep_separator=True,
        )
        self._child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=child_chunk_size,
            chunk_overlap=overlap,
            length_function=len,
            separators=_CHILD_SEPARATORS,
            add_start_index=True,
            strip_whitespace=True,
            keep_separator=True,
        )

    @property
    def name(self) -> str:
        return "parent_child"

    def chunk(
        self,
        text: str,
        document_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        if not text.strip():
            return []

        parent_docs = self._parent_splitter.create_documents([text])
        parents = documents_to_chunks(
            parent_docs,
            source_text=text,
            document_id=document_id,
            metadata=metadata,
        )

        result: list[Chunk] = []
        chunk_index = 0
        for parent in parents:
            parent_id = str(uuid.uuid4())
            parent_meta = {
                **dict(metadata or {}),
                **dict(parent.metadata),
                "is_parent": True,
                "chunk_type": "parent",
                "parent_chunk_id": parent_id,
            }
            parent_chunk = Chunk(
                content=parent.content,
                document_id=document_id,
                chunk_index=chunk_index,
                start_char=parent.start_char,
                end_char=parent.end_char,
                metadata=parent_meta,
                parent_id=None,
            )
            result.append(parent_chunk)
            chunk_index += 1

            child_docs = self._child_splitter.create_documents([parent.content])
            for child_doc in child_docs:
                content = (child_doc.page_content or "").strip()
                if not content:
                    continue
                rel_start = int(child_doc.metadata.get("start_index", 0) or 0)
                # Locate stripped content near relative start inside parent
                found = parent.content.find(content, max(0, rel_start - 8))
                if found != -1:
                    rel_start = found
                abs_start = parent.start_char + rel_start
                abs_end = abs_start + len(content)
                child_meta = {
                    **dict(metadata or {}),
                    "is_parent": False,
                    "chunk_type": "child",
                }
                result.append(
                    Chunk(
                        content=content,
                        document_id=document_id,
                        chunk_index=chunk_index,
                        start_char=abs_start,
                        end_char=abs_end,
                        metadata=child_meta,
                        parent_id=parent_id,
                    )
                )
                chunk_index += 1

        logger.debug(
            "Created %d parent-child chunks from document %s",
            len(result),
            document_id,
        )
        return result

    def get_parent_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Filter to only parent chunks."""
        return [c for c in chunks if c.metadata.get("is_parent", False)]

    def get_child_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """Filter to only child chunks."""
        return [c for c in chunks if not c.metadata.get("is_parent", True)]
