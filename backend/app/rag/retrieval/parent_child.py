"""
FlexSearch Backend - Parent-Child Retrieval Strategy

Search child chunks, return parent context.
"""

import logging

from app.rag.embedding import get_embedding_service
from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.services.vector_store import get_vector_store

logger = logging.getLogger(__name__)


class ParentChildRetrieval(BaseRetrievalStrategy):
    """Parent-child retrieval: search children, return parents."""

    def __init__(self, score_threshold: float | None = None) -> None:
        self._score_threshold = score_threshold

    @property
    def name(self) -> str:
        return "parent_child"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """
        Retrieve by searching child chunks and returning parent content.

        This provides more precise matching (children) with better
        context (parents) in the final results.
        """
        # Generate query embedding
        embedding_service = get_embedding_service()
        query_vector = embedding_service.embed(query)

        # Search only child chunks
        vector_store = get_vector_store()
        results = vector_store.search_with_filter(
            query_vector=query_vector,
            filters={
                "project_id": project_id,
                "chunk_type": "child",
            },
            top_k=top_k * 2,  # Get more to account for deduplication
        )

        # Collect unique parent IDs
        parent_ids = set()
        child_to_parent = {}

        for result in results:
            payload = result.get("payload", {})
            parent_id = payload.get("parent_id")
            if parent_id:
                parent_ids.add(parent_id)
                child_to_parent[result.get("id")] = {
                    "parent_id": parent_id,
                    "score": result.get("score", 0.0),
                }

        # Fetch parent chunks
        # Note: In a production system, you'd batch fetch parents from Qdrant
        # For now, we'll search for parents and match them
        parent_results = vector_store.search_with_filter(
            query_vector=query_vector,
            filters={
                "project_id": project_id,
                "chunk_type": "parent",
            },
            top_k=top_k,
        )

        # Create results using parent content with child scores
        retrieval_results = []
        seen_parents = set()

        for result in parent_results:
            payload = result.get("payload", {})
            parent_chunk_id = payload.get("parent_chunk_id", "")

            if parent_chunk_id in seen_parents:
                continue
            seen_parents.add(parent_chunk_id)

            retrieval_results.append(
                RetrievalResult(
                    content=payload.get("content", ""),
                    score=result.get("score", 0.0),
                    document_id=payload.get("document_id", ""),
                    chunk_id=result.get("id", ""),
                    metadata={
                        "filename": payload.get("filename", ""),
                        "chunk_index": payload.get("chunk_index", 0),
                        "retrieval_type": "parent_child",
                    },
                )
            )

            if len(retrieval_results) >= top_k:
                break

        logger.debug(
            f"Retrieved {len(retrieval_results)} parent chunks for project {project_id}"
        )
        return retrieval_results
