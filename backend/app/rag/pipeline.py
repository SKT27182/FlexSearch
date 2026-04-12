"""
FlexSearch Backend - RAG Pipeline

Main orchestrator for the RAG workflow.
"""

from typing import Any
from uuid import UUID, uuid5, NAMESPACE_DNS

from app.core.config import settings
from app.rag.chunking import (
    BaseChunkingStrategy,
    Chunk,
    FixedWindowChunking,
    ParentChildChunking,
    RecursiveChunking,
    SemanticChunking,
)
from app.rag.embedding import get_embedding_service
from app.rag.ingestion import (
    BaseExtractionStrategy,
    ExtractedContent,
    OCRExtractionStrategy,
    VLMExtractionStrategy,
)
from app.rag.reranking import (
    BaseRerankingStrategy,
    CrossEncoderReranking,
    NoReranking,
)
from app.rag.retrieval import (
    BaseRetrievalStrategy,
    DenseRetrieval,
    HybridRetrieval,
    ParentChildRetrieval,
    RetrievalResult,
)
from app.services.vector_store import get_vector_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class RAGPipeline:
    """
    Main RAG pipeline orchestrator.

    Strategies are selected once per deployment via configuration.
    """

    def __init__(self) -> None:
        # Initialize strategies based on config
        self._extraction = self._create_extraction_strategy()
        self._chunking = self._create_chunking_strategy()
        self._retrieval = self._create_retrieval_strategy()
        self._reranking = self._create_reranking_strategy()
        self._embedding = get_embedding_service()
        self._vector_store = get_vector_store()

        logger.info(
            f"RAG Pipeline initialized: "
            f"extraction={self._extraction.name}, "
            f"chunking={self._chunking.name}, "
            f"retrieval={self._retrieval.name}, "
            f"reranking={self._reranking.name}"
        )

    def _create_extraction_strategy(self) -> BaseExtractionStrategy:
        """Create extraction strategy from config."""
        if settings.extraction_strategy == "vlm":
            return VLMExtractionStrategy()
        return OCRExtractionStrategy()

    def _create_chunking_strategy(self) -> BaseChunkingStrategy:
        """Create chunking strategy from config."""
        match settings.chunking_strategy:
            case "recursive":
                return RecursiveChunking()
            case "semantic":
                return SemanticChunking()
            case "parent_child":
                return ParentChildChunking()
            case _:
                return FixedWindowChunking()

    def _create_retrieval_strategy(self) -> BaseRetrievalStrategy:
        """Create retrieval strategy from config."""
        match settings.retrieval_strategy:
            case "parent_child":
                return ParentChildRetrieval()
            case "hybrid":
                return HybridRetrieval()
            case _:
                return DenseRetrieval()

    def _create_reranking_strategy(self) -> BaseRerankingStrategy:
        """Create reranking strategy from config."""
        if settings.reranking_strategy == "cross_encoder":
            return CrossEncoderReranking()
        return NoReranking()

    async def ingest_document(
        self,
        content: bytes,
        content_type: str,
        filename: str,
        document_id: str,
        project_id: str,
    ) -> int:
        """
        Ingest a document into the RAG system.

        Args:
            content: Raw file bytes
            content_type: MIME type
            filename: Original filename
            document_id: Document ID for tracking
            project_id: Project to add document to

        Returns:
            Number of chunks created
        """
        logger.info(f"Ingesting document: {filename} (project: {project_id})")

        # Extract text
        extracted = await self._extraction.extract(content, content_type, filename)

        if extracted.is_empty:
            logger.warning(f"No text extracted from {filename}")
            return 0

        # Chunk the text
        chunks = self._chunking.chunk(
            text=extracted.text,
            document_id=document_id,
            metadata={
                "filename": filename,
                "project_id": project_id,
                "page_count": extracted.page_count,
            },
        )

        if not chunks:
            logger.warning(f"No chunks created from {filename}")
            return 0

        # Generate embeddings
        chunk_texts = [chunk.content for chunk in chunks]
        embeddings = self._embedding.embed_batch(chunk_texts)

        # Prepare for vector store
        # Generate deterministic UUIDs for each chunk using UUID5
        # This ensures Qdrant compatibility (requires UUID or int, not string)
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

        # Store in vector database
        self._vector_store.upsert_vectors(ids, embeddings, payloads)

        logger.info(f"Ingested {len(chunks)} chunks from {filename}")
        return len(chunks)

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """
        Retrieve relevant chunks for a query.

        Args:
            query: User query
            project_id: Project to search
            top_k: Number of results

        Returns:
            List of retrieval results
        """
        # Retrieve
        results = await self._retrieval.retrieve(
            query=query,
            project_id=project_id,
            top_k=top_k * 2,  # Get more for reranking
        )

        # Rerank
        reranked = await self._reranking.rerank(
            query=query,
            results=results,
            top_k=top_k,
        )

        return reranked

    async def query(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """
        Full RAG query: retrieve + format context.

        Args:
            query: User query
            project_id: Project to search
            top_k: Number of results

        Returns:
            Dict with context and sources
        """
        results = await self.retrieve(query, project_id, top_k)

        context_chunks = [
            {
                "content": r.content,
                "score": r.score,
                "metadata": r.metadata,
            }
            for r in results
        ]

        return {
            "context": "\n\n".join(r.content for r in results),
            "chunks": context_chunks,
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
        """Delete all data for a project."""
        self._vector_store.delete_by_project(project_id)
        logger.info(f"Deleted RAG data for project: {project_id}")

    def delete_document_data(self, document_id: str) -> None:
        """Delete all data for a document."""
        self._vector_store.delete_by_document(document_id)
        logger.info(f"Deleted RAG data for document: {document_id}")

    @property
    def retrieval_strategy(self) -> str:
        """Get active retrieval strategy name."""
        return self._retrieval.name


# Singleton instance
_rag_pipeline: RAGPipeline | None = None


def get_rag_pipeline() -> RAGPipeline:
    """Get RAG pipeline singleton."""
    global _rag_pipeline
    if _rag_pipeline is None:
        _rag_pipeline = RAGPipeline()
    return _rag_pipeline
