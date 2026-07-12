"""
FlexSearch Backend - Hybrid Retrieval Strategy

OpenSearch dense knn + BM25 with Reciprocal Rank Fusion.
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


class HybridRetrieval(BaseRetrievalStrategy):
    """Hybrid retrieval combining dense vector and BM25 sparse search."""

    def __init__(
        self,
        rrf_k: int = 60,
        *,
        hierarchy_mode: HierarchyRetrievalMode = "chunks_only",
    ) -> None:
        self._rrf_k = rrf_k
        self._hierarchy_mode = hierarchy_mode

    @property
    def name(self) -> str:
        return "hybrid"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        embedding_service = get_embedding_service()
        query_vector = embedding_service.embed(query)
        store = get_search_store()
        hits = store.hybrid_search(
            query=query,
            query_vector=query_vector,
            filters=filters_for_hierarchy(project_id, self._hierarchy_mode),
            top_k=top_k,
            rrf_k=self._rrf_k,
        )

        retrieval_results = []
        for hit in hits:
            result = hit_to_result(hit)
            result.metadata["retrieval_type"] = "hybrid"
            result.metadata["rrf_score"] = hit.payload.get("rrf_score", hit.score)
            retrieval_results.append(result)

        retrieval_results = apply_hierarchy_postprocess(
            retrieval_results, self._hierarchy_mode
        )[:top_k]

        logger.debug(
            "Hybrid retrieval returned %d results for project %s (mode=%s)",
            len(retrieval_results),
            project_id,
            self._hierarchy_mode,
        )
        return retrieval_results

    @staticmethod
    def reciprocal_rank_fusion(
        result_lists: list[list[RetrievalResult]],
        k: int = 60,
    ) -> list[RetrievalResult]:
        """Public RRF helper kept for unit tests / callers."""
        scores: dict[str, float] = {}
        result_map: dict[str, RetrievalResult] = {}

        for results in result_lists:
            for rank, result in enumerate(results):
                chunk_id = result.chunk_id
                rrf_score = 1.0 / (k + rank + 1)
                scores[chunk_id] = scores.get(chunk_id, 0) + rrf_score
                if (
                    chunk_id not in result_map
                    or result.score > result_map[chunk_id].score
                ):
                    result_map[chunk_id] = result

        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        final_results = []
        for chunk_id in sorted_ids:
            result = result_map[chunk_id]
            result.metadata["original_score"] = result.score
            result.metadata["rrf_score"] = scores[chunk_id]
            result.score = scores[chunk_id]
            final_results.append(result)
        return final_results
