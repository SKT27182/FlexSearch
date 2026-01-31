"""
FlexSearch Backend - Hybrid Retrieval Strategy

Combined dense vector and BM25 sparse retrieval with RRF fusion.
"""

import math
import re
from collections import Counter
from typing import Any

from app.rag.embedding import get_embedding_service
from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.dense import DenseRetrieval
from app.services.vector_store import get_vector_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class BM25:
    """
    BM25 implementation for sparse retrieval.

    BM25 scoring formula:
    score(D, Q) = Σ IDF(qi) * (f(qi, D) * (k1 + 1)) / (f(qi, D) + k1 * (1 - b + b * |D| / avgdl))
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self._k1 = k1
        self._b = b
        self._documents: list[list[str]] = []
        self._doc_ids: list[str] = []
        self._doc_payloads: list[dict[str, Any]] = []
        self._avgdl: float = 0.0
        self._doc_freqs: dict[str, int] = {}
        self._idf: dict[str, float] = {}
        self._doc_len: list[int] = []

    def _tokenize(self, text: str) -> list[str]:
        """Simple tokenization with lowercasing."""
        text = text.lower()
        tokens = re.findall(r"\b\w+\b", text)
        return tokens

    def fit(
        self,
        documents: list[str],
        doc_ids: list[str],
        payloads: list[dict[str, Any]],
    ) -> None:
        """Build BM25 index from documents."""
        self._documents = [self._tokenize(doc) for doc in documents]
        self._doc_ids = doc_ids
        self._doc_payloads = payloads
        self._doc_len = [len(doc) for doc in self._documents]
        self._avgdl = (
            sum(self._doc_len) / len(self._documents) if self._documents else 0
        )

        # Calculate document frequencies
        self._doc_freqs = {}
        for doc in self._documents:
            seen = set()
            for token in doc:
                if token not in seen:
                    self._doc_freqs[token] = self._doc_freqs.get(token, 0) + 1
                    seen.add(token)

        # Calculate IDF scores
        n_docs = len(self._documents)
        self._idf = {}
        for token, df in self._doc_freqs.items():
            self._idf[token] = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float, dict]]:
        """
        Search documents using BM25.

        Returns:
            List of (doc_id, score, payload) tuples
        """
        query_tokens = self._tokenize(query)
        scores = []

        for i, doc in enumerate(self._documents):
            score = 0.0
            doc_len = self._doc_len[i]
            term_freqs = Counter(doc)

            for token in query_tokens:
                if token not in self._idf:
                    continue

                tf = term_freqs.get(token, 0)
                idf = self._idf[token]

                # BM25 scoring
                numerator = tf * (self._k1 + 1)
                denominator = tf + self._k1 * (
                    1 - self._b + self._b * doc_len / self._avgdl
                )
                score += idf * (numerator / denominator)

            scores.append((self._doc_ids[i], score, self._doc_payloads[i]))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


class HybridRetrieval(BaseRetrievalStrategy):
    """
    Hybrid retrieval combining dense vector and BM25 sparse search.

    Uses Reciprocal Rank Fusion (RRF) to combine results from both methods.
    """

    def __init__(
        self,
        dense_weight: float = 0.7,
        sparse_weight: float = 0.3,
        rrf_k: int = 60,
    ) -> None:
        """
        Initialize hybrid retrieval.

        Args:
            dense_weight: Weight for dense retrieval scores (not used in RRF)
            sparse_weight: Weight for sparse retrieval scores (not used in RRF)
            rrf_k: Constant for RRF formula (typically 60)
        """
        self._dense_weight = dense_weight
        self._sparse_weight = sparse_weight
        self._rrf_k = rrf_k
        self._dense_retriever = DenseRetrieval()
        self._bm25: BM25 | None = None
        self._bm25_project_id: str | None = None

    @property
    def name(self) -> str:
        return "hybrid"

    async def _build_bm25_index(self, project_id: str) -> None:
        """Build BM25 index for a project by fetching all chunks."""
        vector_store = get_vector_store()

        # Get all chunks for the project using a dummy query
        # Note: In production, you'd have a separate document store
        embedding_service = get_embedding_service()
        dummy_vector = [0.0] * embedding_service.dimension

        # Fetch a large number of results to build index
        results = vector_store.search(
            query_vector=dummy_vector,
            project_id=project_id,
            top_k=10000,  # Get all chunks
            score_threshold=0.0,  # No threshold
        )

        if not results:
            logger.warning(f"No documents found for project {project_id}")
            self._bm25 = None
            return

        documents = []
        doc_ids = []
        payloads = []

        for result in results:
            payload = result.get("payload", {})
            content = payload.get("content", "")
            if content:
                documents.append(content)
                doc_ids.append(result.get("id", ""))
                payloads.append(payload)

        self._bm25 = BM25()
        self._bm25.fit(documents, doc_ids, payloads)
        self._bm25_project_id = project_id

        logger.info(
            f"Built BM25 index with {len(documents)} documents for project {project_id}"
        )

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        """
        Hybrid retrieval using both dense and sparse search.

        1. Perform dense vector search
        2. Perform BM25 sparse search
        3. Combine results using Reciprocal Rank Fusion
        """
        # Rebuild BM25 index if project changed
        if self._bm25_project_id != project_id:
            await self._build_bm25_index(project_id)

        # Get more results for fusion
        fetch_k = top_k * 3

        # Dense retrieval
        dense_results = await self._dense_retriever.retrieve(
            query=query,
            project_id=project_id,
            top_k=fetch_k,
        )

        # Sparse retrieval (BM25)
        sparse_results = []
        if self._bm25:
            bm25_results = self._bm25.search(query, top_k=fetch_k)
            for doc_id, score, payload in bm25_results:
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

        # Combine using RRF
        if not sparse_results:
            # Fall back to dense only if no BM25 results
            logger.debug("No BM25 results, using dense-only")
            combined = dense_results
        else:
            combined = self.reciprocal_rank_fusion(
                [dense_results, sparse_results],
                k=self._rrf_k,
            )

        # Add hybrid metadata
        for result in combined[:top_k]:
            result.metadata["retrieval_type"] = "hybrid"

        logger.debug(
            f"Hybrid retrieval: dense={len(dense_results)}, "
            f"sparse={len(sparse_results)}, combined={len(combined)}"
        )

        return combined[:top_k]

    @staticmethod
    def reciprocal_rank_fusion(
        result_lists: list[list[RetrievalResult]],
        k: int = 60,
    ) -> list[RetrievalResult]:
        """
        Combine multiple result lists using Reciprocal Rank Fusion.

        RRF score = sum(1 / (k + rank)) for each result list

        Args:
            result_lists: List of result lists to combine
            k: Constant for RRF (typically 60)

        Returns:
            Combined and re-ranked results
        """
        scores: dict[str, float] = {}
        result_map: dict[str, RetrievalResult] = {}

        for results in result_lists:
            for rank, result in enumerate(results):
                chunk_id = result.chunk_id
                rrf_score = 1.0 / (k + rank + 1)
                scores[chunk_id] = scores.get(chunk_id, 0) + rrf_score

                # Keep the result with higher original score
                if (
                    chunk_id not in result_map
                    or result.score > result_map[chunk_id].score
                ):
                    result_map[chunk_id] = result

        # Sort by combined RRF score
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

        # Create final results with RRF scores
        final_results = []
        for chunk_id in sorted_ids:
            result = result_map[chunk_id]
            # Store original score and use RRF score as primary
            result.metadata["original_score"] = result.score
            result.metadata["rrf_score"] = scores[chunk_id]
            result.score = scores[chunk_id]
            final_results.append(result)

        return final_results
