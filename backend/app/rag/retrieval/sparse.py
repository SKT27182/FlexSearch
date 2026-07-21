"""
FlexSearch Backend - BM25-only (lexical) retrieval via OpenSearch.
"""

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.hierarchy import (
    apply_hierarchy_postprocess,
    filters_for_hierarchy,
    hit_to_result,
)
from app.schemas.rag_config import HierarchyRetrievalMode
from app.services.search_store import get_search_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class SparseRetrieval(BaseRetrievalStrategy):
    """Lexical retrieval using OpenSearch BM25 (no dense vectors)."""

    def __init__(
        self,
        k1: float = 1.5,
        b: float = 0.75,
        *,
        hierarchy_mode: HierarchyRetrievalMode = "chunks_only",
    ) -> None:
        # k1/b retained for rag_config factory compatibility; OpenSearch
        # uses its own BM25 similarity settings at the index level.
        self._k1 = k1
        self._b = b
        self._hierarchy_mode = hierarchy_mode

    @property
    def name(self) -> str:
        return "bm25"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
        rag_generation: int | None = None,
    ) -> list[RetrievalResult]:
        store = get_search_store()
        hits = store.bm25_search(
            query=query,
            filters=filters_for_hierarchy(
                project_id, self._hierarchy_mode, rag_generation=rag_generation
            ),
            top_k=top_k,
        )

        retrieval_results = []
        for hit in hits:
            result = hit_to_result(hit)
            result.metadata["retrieval_type"] = "bm25"
            retrieval_results.append(result)

        retrieval_results = apply_hierarchy_postprocess(
            retrieval_results, self._hierarchy_mode
        )[:top_k]

        logger.debug(
            "BM25 retrieved %d results for project %s (mode=%s)",
            len(retrieval_results),
            project_id,
            self._hierarchy_mode,
        )
        return retrieval_results
