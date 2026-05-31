"""
FlexSearch Backend - BM25-only (lexical) retrieval strategy.
"""

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.bm25_index import BM25, build_project_bm25_index
from app.utils.logger import create_logger

logger = create_logger(__name__)


class SparseRetrieval(BaseRetrievalStrategy):
    """Lexical retrieval using BM25 only (no dense vectors)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._bm25: BM25 | None = None
        self._bm25_project_id: str | None = None

    @property
    def name(self) -> str:
        return "bm25"

    async def _ensure_index(self, project_id: str) -> None:
        if self._bm25_project_id == project_id and self._bm25 is not None:
            return
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
        await self._ensure_index(project_id)

        if not self._bm25:
            logger.warning("BM25 index empty for project %s", project_id)
            return []

        retrieval_results: list[RetrievalResult] = []
        for doc_id, score, payload in self._bm25.search(query, top_k=top_k):
            retrieval_results.append(
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

        logger.debug(
            "BM25 retrieved %d results for project %s",
            len(retrieval_results),
            project_id,
        )
        return retrieval_results
