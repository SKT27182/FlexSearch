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
from app.services.search_store import get_search_store
from app.services.search_store.types import SearchDocument
from app.observability.metrics import metrics
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
        self._search_store = get_search_store()
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
        chunks = self._chunking.chunk(
            text=text,
            document_id=document_id,
            metadata={
                "filename": filename,
                "project_id": project_id,
                "page_count": page_count,
            },
        )
        # Hierarchy metadata (heading breadcrumbs) when enabled
        if isinstance(self._config, VectorRagConfig) and self._config.extraction.extract_hierarchy:
            from app.rag.ingestion.hierarchy import annotate_chunks_with_hierarchy

            annotate_chunks_with_hierarchy(text, chunks)
        return chunks

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
        documents: list[SearchDocument] = []
        for chunk, embedding in zip(chunks, embeddings):
            meta = dict(chunk.metadata)
            chunk_type = meta.pop("chunk_type", None)
            parent_chunk_id = meta.pop("parent_chunk_id", None)
            meta.pop("is_parent", None)
            # Parents are stored under their stable parent_chunk_id so children
            # can resolve them via get_by_ids(parent_id).
            if chunk_type == "parent" and parent_chunk_id:
                doc_id = parent_chunk_id
            else:
                doc_id = str(
                    uuid5(NAMESPACE_DNS, f"{document_id}_{chunk.chunk_index}")
                )
            documents.append(
                SearchDocument(
                    id=doc_id,
                    embedding=embedding,
                    content=chunk.content,
                    project_id=project_id,
                    document_id=document_id,
                    chunk_index=chunk.chunk_index,
                    chunk_type=chunk_type,
                    parent_id=chunk.parent_id,
                    summary_level="chunk",
                    filename=filename,
                    start_char=chunk.start_char,
                    end_char=chunk.end_char,
                    extra=meta,
                )
            )
        self._search_store.upsert(documents)
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
        import time

        started = time.perf_counter()
        if self._rag_mode == RagMode.GRAPH:
            assert isinstance(self._config, GraphRagConfig)
            effective = GraphEffectiveRagConfig.for_retrieval(
                self._config, overrides, top_k=top_k
            )
            # Pass full effective config so graph_backend reaches the factory
            # (bare GraphRetrievalConfig defaults to neo4j).
            retrieval = build_graph_retrieval_strategy(effective)
            k = effective.top_k
            results = await retrieval.retrieve(
                query=query,
                project_id=project_id,
                top_k=k,
            )
            metrics.record_retrieval(
                strategy=retrieval.name,
                hit_count=len(results),
                seconds=time.perf_counter() - started,
                rag_mode="graph",
            )
            return results, retrieval.name, "none"

        assert isinstance(self._config, VectorRagConfig)
        effective = EffectiveRagConfig.for_retrieval(
            self._config, overrides, top_k=top_k
        )
        hierarchy_mode = effective.summaries.retrieval_mode
        retrieval = build_retrieval_strategy(
            effective.retrieval,
            rag_mode=self._rag_mode,
            hierarchy_mode=hierarchy_mode,
        )
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
        metrics.record_retrieval(
            strategy=retrieval.name,
            hit_count=len(reranked),
            seconds=time.perf_counter() - started,
            rag_mode="vector",
        )
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
            self._search_store.delete_by_project(project_id)
            logger.info("Deleted OpenSearch data for project: %s", project_id)

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
            self._search_store.delete_by_document(document_id)
            logger.info("Deleted OpenSearch data for document: %s", document_id)


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
