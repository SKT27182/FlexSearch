"""
FlexSearch Backend - Parent-Child Retrieval Strategy

Search child chunks, fetch parents by id, score by best child.
"""

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.schemas.rag_config import HierarchyRetrievalMode
from app.services.embedding import get_embedding_service
from app.services.search_store import get_search_store
from app.services.search_store.types import SearchFilters
from app.utils.logger import create_logger

logger = create_logger(__name__)


class ParentChildRetrieval(BaseRetrievalStrategy):
    """Parent-child retrieval: search children, return parents."""

    def __init__(
        self,
        score_threshold: float | None = None,
        *,
        hierarchy_mode: HierarchyRetrievalMode = "chunks_only",
    ) -> None:
        self._score_threshold = score_threshold
        # Parent-child always searches chunk-level children; hierarchy_mode
        # is accepted for factory API symmetry but does not change filters.
        self._hierarchy_mode = hierarchy_mode

    @property
    def name(self) -> str:
        return "parent_child"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
        rag_generation: int | None = None,
    ) -> list[RetrievalResult]:
        """
        Search child chunks, resolve parents via get_by_ids, score by best child.
        """
        embedding_service = get_embedding_service()
        query_vector = embedding_service.embed(query)
        store = get_search_store()

        child_hits = store.dense_search(
            query_vector=query_vector,
            filters=SearchFilters(
                project_id=project_id,
                rag_generation=rag_generation,
                chunk_type="child",
                summary_level="chunk",
            ),
            top_k=top_k * 2,
            score_threshold=self._score_threshold,
        )

        # parent_id -> best child score (+ first child metadata)
        best_child: dict[str, tuple[float, str]] = {}
        for hit in child_hits:
            parent_id = hit.parent_id
            if not parent_id:
                continue
            prev = best_child.get(parent_id)
            if prev is None or hit.score > prev[0]:
                best_child[parent_id] = (hit.score, hit.id)

        if not best_child:
            logger.debug("No child hits with parent_id for project %s", project_id)
            return []

        # Prefer parents ordered by best child score
        ordered_parent_ids = sorted(
            best_child.keys(),
            key=lambda pid: best_child[pid][0],
            reverse=True,
        )[:top_k]

        parents = store.get_by_ids(ordered_parent_ids)
        parent_by_id = {p.id: p for p in parents}

        retrieval_results: list[RetrievalResult] = []
        for parent_id in ordered_parent_ids:
            parent = parent_by_id.get(parent_id)
            if parent is None:
                logger.warning("Parent chunk %s not found in index", parent_id)
                continue
            child_score, child_id = best_child[parent_id]
            retrieval_results.append(
                RetrievalResult(
                    content=parent.content,
                    score=child_score,
                    document_id=parent.document_id,
                    chunk_id=parent.id,
                    metadata={
                        "filename": parent.filename,
                        "chunk_index": parent.chunk_index,
                        "summary_level": parent.summary_level,
                        "retrieval_type": "parent_child",
                        "matched_child_id": child_id,
                        "child_score": child_score,
                    },
                )
            )

        logger.debug(
            "Retrieved %d parent chunks for project %s",
            len(retrieval_results),
            project_id,
        )
        return retrieval_results
