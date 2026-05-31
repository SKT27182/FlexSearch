"""
FlexSearch Backend - Hybrid Retrieval Strategy

Combined dense vector and BM25 sparse retrieval with RRF fusion.
"""

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.bm25_index import BM25, build_project_bm25_index
from app.rag.retrieval.dense import DenseRetrieval
from app.utils.logger import create_logger

logger = create_logger(__name__)


class HybridRetrieval(BaseRetrievalStrategy):
    """
    Hybrid retrieval combining dense vector and BM25 sparse search.

    Uses Reciprocal Rank Fusion (RRF) to combine results from both methods.
    """

    def __init__(self, rrf_k: int = 60, k1: float = 1.5, b: float = 0.75) -> None:
        self._rrf_k = rrf_k
        self._k1 = k1
        self._b = b
        self._dense_retriever = DenseRetrieval()
        self._bm25: BM25 | None = None
        self._bm25_project_id: str | None = None

    @property
    def name(self) -> str:
        return "hybrid"

    async def _build_bm25_index(self, project_id: str) -> None:
        self._bm25 = await build_project_bm25_index(
            project_id, k1=self._k1, b=self._b
        )
        self._bm25_project_id = project_id

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        if self._bm25_project_id != project_id:
            await self._build_bm25_index(project_id)

        fetch_k = top_k * 3

        dense_results = await self._dense_retriever.retrieve(
            query=query,
            project_id=project_id,
            top_k=fetch_k,
        )

        sparse_results: list[RetrievalResult] = []
        if self._bm25:
            for doc_id, score, payload in self._bm25.search(query, top_k=fetch_k):
                sparse_results.append(
                    RetrievalResult(
                        content=payload.get("content", ""),
                        score=score,
                        document_id=payload.get("document_id", ""),
                        chunk_id=doc_id,
                        metadata={
                            "filename": payload.get("filename", ""),
                            "chunk_index": payload.get("chunk_index", 0),
                            "retrieval_type": "bm25",
                        },
                    )
                )

        if not sparse_results:
            logger.debug("No BM25 results, using dense-only")
            combined = dense_results
        else:
            combined = self.reciprocal_rank_fusion(
                [dense_results, sparse_results],
                k=self._rrf_k,
            )

        for result in combined[:top_k]:
            result.metadata["retrieval_type"] = "hybrid"

        logger.debug(
            "Hybrid retrieval: dense=%d, sparse=%d, combined=%d",
            len(dense_results),
            len(sparse_results),
            len(combined),
        )

        return combined[:top_k]

    @staticmethod
    def reciprocal_rank_fusion(
        result_lists: list[list[RetrievalResult]],
        k: int = 60,
    ) -> list[RetrievalResult]:
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
