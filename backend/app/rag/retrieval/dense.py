"""
FlexSearch Backend - Dense Retrieval Strategy

Vector similarity search using OpenSearch knn.
"""

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.hierarchy import (
    apply_hierarchy_postprocess,
    filters_for_hierarchy,
    hit_to_result,
)
from app.schemas.rag_config import HierarchyRetrievalMode
from app.services.embedding import get_embedding_service
from app.services.search_store import get_search_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class DenseRetrieval(BaseRetrievalStrategy):
    """Dense vector retrieval using embeddings."""

    def __init__(
        self,
        score_threshold: float | None = None,
        *,
        hierarchy_mode: HierarchyRetrievalMode = "chunks_only",
    ) -> None:
        self._score_threshold = score_threshold
        self._hierarchy_mode = hierarchy_mode

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
        embedding_service = get_embedding_service()
        query_vector = embedding_service.embed(query)

        store = get_search_store()
        hits = store.dense_search(
            query_vector=query_vector,
            filters=filters_for_hierarchy(project_id, self._hierarchy_mode),
            top_k=top_k,
            score_threshold=self._score_threshold,
        )

        retrieval_results = [hit_to_result(hit) for hit in hits]
        retrieval_results = apply_hierarchy_postprocess(
            retrieval_results, self._hierarchy_mode
        )[:top_k]

        logger.debug(
            "Retrieved %d dense results for project %s (mode=%s)",
            len(retrieval_results),
            project_id,
            self._hierarchy_mode,
        )
        return retrieval_results
