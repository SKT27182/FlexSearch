"""
FlexSearch Backend - RAG Pipeline

Main orchestrator for the RAG workflow.
"""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from app.rag.chunking.base import Chunk
from app.rag.embedding import get_embedding_service
from app.rag.factory import (
    build_chunking_strategy,
    build_extraction_strategy,
    build_reranking_strategy,
    build_retrieval_strategy,
)
from app.rag.ingestion.base import ExtractedContent, ExtractionProgressCallback
from app.rag.retrieval.base import RetrievalResult
from app.schemas.rag_config import EffectiveRagConfig, RagConfig, RetrievalOverrides
from app.services.vector_store import get_vector_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class RAGPipeline:
    """RAG pipeline with per-project configuration."""

    def __init__(self, config: RagConfig) -> None:
        self._config = config
        self._extraction = build_extraction_strategy(config.extraction)
        self._chunking = build_chunking_strategy(config.chunking)
        self._embedding = get_embedding_service()
        self._vector_store = get_vector_store()

    @property
    def config(self) -> RagConfig:
        return self._config

    async def extract_document(
        self,
        content: bytes,
        content_type: str,
        filename: str,
        *,
        on_progress: ExtractionProgressCallback | None = None,
    ) -> ExtractedContent:
        return await self._extraction.extract(
            content,
            content_type,
            filename,
            on_progress=on_progress,
        )

    def chunk_text(
        self,
        text: str,
        document_id: str,
        filename: str,
        project_id: str,
        page_count: int = 0,
    ) -> list[Chunk]:
        return self._chunking.chunk(
            text=text,
            document_id=document_id,
            metadata={
                "filename": filename,
                "project_id": project_id,
                "page_count": page_count,
            },
        )

    def index_chunks(
        self,
        chunks: list[Chunk],
        document_id: str,
        project_id: str,
        filename: str,
    ) -> int:
        if not chunks:
            return 0
        chunk_texts = [chunk.content for chunk in chunks]
        embeddings = self._embedding.embed_batch(chunk_texts)
        ids = [
            str(uuid5(NAMESPACE_DNS, f"{document_id}_{chunk.chunk_index}"))
            for chunk in chunks
        ]
        payloads = [
            {
                "content": chunk.content,
                "document_id": document_id,
                "project_id": project_id,
                "chunk_index": chunk.chunk_index,
                "filename": filename,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "parent_id": chunk.parent_id,
                **chunk.metadata,
            }
            for chunk in chunks
        ]
        self._vector_store.upsert_vectors(ids, embeddings, payloads)
        return len(chunks)

    async def ingest_from_text(
        self,
        text: str,
        document_id: str,
        project_id: str,
        filename: str,
        page_count: int = 0,
    ) -> int:
        chunks = self.chunk_text(text, document_id, filename, project_id, page_count)
        return self.index_chunks(chunks, document_id, project_id, filename)

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
        overrides: RetrievalOverrides | None = None,
    ) -> tuple[list[RetrievalResult], str, str]:
        effective = EffectiveRagConfig.for_retrieval(
            self._config, overrides, top_k=top_k
        )
        retrieval = build_retrieval_strategy(effective.retrieval)
        reranking = build_reranking_strategy(effective.reranking)
        k = effective.top_k

        results = await retrieval.retrieve(
            query=query,
            project_id=project_id,
            top_k=k * 2,
        )
        reranked = await reranking.rerank(query=query, results=results, top_k=k)
        return reranked, retrieval.name, reranking.name

    async def query(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
        overrides: RetrievalOverrides | None = None,
    ) -> dict[str, Any]:
        results, _, _ = await self.retrieve(query, project_id, top_k, overrides)
        return {
            "context": "\n\n".join(r.content for r in results),
            "chunks": [
                {"content": r.content, "score": r.score, "metadata": r.metadata}
                for r in results
            ],
            "sources": [
                {
                    "filename": r.metadata.get("filename", ""),
                    "chunk_index": r.metadata.get("chunk_index", 0),
                    "content": r.content,
                    "score": r.score,
                }
                for r in results
            ],
        }

    def delete_project_data(self, project_id: str) -> None:
        self._vector_store.delete_by_project(project_id)
        logger.info(f"Deleted RAG data for project: {project_id}")

    def delete_document_data(self, document_id: str) -> None:
        self._vector_store.delete_by_document(document_id)
        logger.info(f"Deleted RAG data for document: {document_id}")


def create_pipeline(config: RagConfig) -> RAGPipeline:
    return RAGPipeline(config)


def get_rag_pipeline(config: RagConfig | None = None) -> RAGPipeline:
    """Build pipeline from config or deployment defaults."""
    from app.schemas.rag_config import RagConfig as RC

    return create_pipeline(config or RC.from_settings())
