"""
FlexSearch Backend - Dense Retrieval Strategy

Vector similarity search using Qdrant.
"""

import logging

from app.rag.embedding import get_embedding_service
from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)


class DenseRetrieval(BaseRetrievalStrategy):
    """Dense vector retrieval using embeddings."""

    def __init__(self, score_threshold: float | None = None) -> None:
        self._score_threshold = score_threshold

    @property
    def name(self) -> str:
        return "dense"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """Retrieve using dense vector search."""
        # Generate query embedding
        embedding_service = get_embedding_service()
        query_vector = embedding_service.embed(query)

        # Search in Qdrant
        vector_store = get_vector_store()
        results = vector_store.search(
            query_vector=query_vector,
            project_id=project_id,
            top_k=top_k,
            score_threshold=self._score_threshold,
        )

        # Convert to RetrievalResult
        retrieval_results = []
        for result in results:
            payload = result.get("payload", {})
            retrieval_results.append(
                RetrievalResult(
                    content=payload.get("content", ""),
                    score=result.get("score", 0.0),
                    document_id=payload.get("document_id", ""),
                    chunk_id=result.get("id", ""),
                    metadata={
                        "filename": payload.get("filename", ""),
                        "chunk_index": payload.get("chunk_index", 0),
                    },
                )
            )

        logger.debug(
            f"Retrieved {len(retrieval_results)} results for project {project_id}"
        )
        return retrieval_results
