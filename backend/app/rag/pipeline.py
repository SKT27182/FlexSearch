"""
FlexSearch Backend - RAG Pipeline

Main orchestrator for the RAG workflow.
"""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_DNS, uuid5

from app.db.models import RagMode
from app.rag.chunking.base import Chunk
from app.services.embedding import get_embedding_service
from app.rag.factory import (
    build_chunking_strategy,
    build_extraction_strategy,
    build_graph_retrieval_strategy,
    build_reranking_strategy,
    build_retrieval_strategy,
)
from app.rag.ingestion.base import ExtractedContent, ExtractionProgressCallback
from app.rag.retrieval.base import RetrievalResult
from app.schemas.rag_config import (
    EffectiveRagConfig,
    GraphEffectiveRagConfig,
    GraphRagConfig,
    RagConfig,
    RetrievalOverrides,
    VectorRagConfig,
)
from app.services.neo4j_store import get_neo4j_store
from app.services.vector_store import get_vector_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class RAGPipeline:
    """RAG pipeline with per-project configuration and mode."""

    def __init__(
        self,
        config: VectorRagConfig | GraphRagConfig,
        rag_mode: RagMode = RagMode.VECTOR,
    ) -> None:
        self._config = config
        self._rag_mode = rag_mode
        self._extraction = build_extraction_strategy(config.extraction)
        self._embedding = get_embedding_service()
        self._vector_store = get_vector_store()
        self._neo4j = get_neo4j_store()
        if rag_mode == RagMode.VECTOR:
            assert isinstance(config, VectorRagConfig)
            self._chunking = build_chunking_strategy(config.chunking)
        else:
            self._chunking = None

    @property
    def config(self) -> VectorRagConfig | GraphRagConfig:
        return self._config

    @property
    def rag_mode(self) -> RagMode:
        return self._rag_mode

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
        if self._chunking is None:
            raise RuntimeError("chunk_text is only available for vector mode")
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
        if self._rag_mode == RagMode.GRAPH:
            raise RuntimeError("index_chunks is not used in graph mode")
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
        if self._rag_mode == RagMode.GRAPH:
            assert isinstance(self._config, GraphRagConfig)
            effective = GraphEffectiveRagConfig.for_retrieval(
                self._config, overrides, top_k=top_k
            )
            retrieval = build_graph_retrieval_strategy(effective.retrieval)
            k = effective.top_k
            results = await retrieval.retrieve(
                query=query,
                project_id=project_id,
                top_k=k,
            )
            return results, retrieval.name, "none"

        assert isinstance(self._config, VectorRagConfig)
        effective = EffectiveRagConfig.for_retrieval(
            self._config, overrides, top_k=top_k
        )
        retrieval = build_retrieval_strategy(effective.retrieval, rag_mode=self._rag_mode)
        reranking = build_reranking_strategy(
            effective.reranking, rag_mode=self._rag_mode
        )
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
        if self._rag_mode == RagMode.GRAPH:
            self._neo4j.delete_project_subgraph(project_id)
            logger.info("Deleted Neo4j graph for project: %s", project_id)
        else:
            self._vector_store.delete_by_project(project_id)
            logger.info("Deleted Qdrant data for project: %s", project_id)

    def delete_document_data(self, document_id: str, project_id: str | None = None) -> None:
        if self._rag_mode == RagMode.GRAPH:
            if not project_id:
                logger.warning(
                    "Graph document delete requires project_id for %s",
                    document_id,
                )
                return
            self._neo4j.delete_document_subgraph(project_id, document_id)
            logger.info("Deleted Neo4j data for document: %s", document_id)
        else:
            self._vector_store.delete_by_document(document_id)
            logger.info("Deleted Qdrant data for document: %s", document_id)


def create_pipeline(
    config: VectorRagConfig | GraphRagConfig,
    rag_mode: RagMode = RagMode.VECTOR,
) -> RAGPipeline:
    return RAGPipeline(config, rag_mode=rag_mode)


def get_rag_pipeline(
    config: VectorRagConfig | GraphRagConfig | None = None,
    rag_mode: RagMode = RagMode.VECTOR,
) -> RAGPipeline:
    """Build pipeline from config or deployment defaults."""
    from app.schemas.rag_config import VectorRagConfig as VC

    return create_pipeline(config or VC.from_settings(), rag_mode=rag_mode)
