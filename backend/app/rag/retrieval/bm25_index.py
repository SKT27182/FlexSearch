"""
BM25 lexical index built from project chunks stored in Qdrant.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from app.rag.embedding import get_embedding_service
from app.services.vector_store import get_vector_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class BM25:
    """
    BM25 sparse retrieval over in-memory tokenized chunks.

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
        text = text.lower()
        return re.findall(r"\b\w+\b", text)

    def fit(
        self,
        documents: list[str],
        doc_ids: list[str],
        payloads: list[dict[str, Any]],
    ) -> None:
        self._documents = [self._tokenize(doc) for doc in documents]
        self._doc_ids = doc_ids
        self._doc_payloads = payloads
        self._doc_len = [len(doc) for doc in self._documents]
        self._avgdl = (
            sum(self._doc_len) / len(self._documents) if self._documents else 0
        )

        self._doc_freqs = {}
        for doc in self._documents:
            seen = set()
            for token in doc:
                if token not in seen:
                    self._doc_freqs[token] = self._doc_freqs.get(token, 0) + 1
                    seen.add(token)

        n_docs = len(self._documents)
        self._idf = {}
        for token, df in self._doc_freqs.items():
            self._idf[token] = math.log((n_docs - df + 0.5) / (df + 0.5) + 1)

    def search(self, query: str, top_k: int = 10) -> list[tuple[str, float, dict]]:
        query_tokens = self._tokenize(query)
        scores: list[tuple[str, float, dict]] = []

        for i, doc in enumerate(self._documents):
            score = 0.0
            doc_len = self._doc_len[i]
            term_freqs = Counter(doc)

            for token in query_tokens:
                if token not in self._idf:
                    continue
                tf = term_freqs.get(token, 0)
                idf = self._idf[token]
                numerator = tf * (self._k1 + 1)
                denominator = tf + self._k1 * (
                    1 - self._b + self._b * doc_len / self._avgdl
                )
                score += idf * (numerator / denominator)

            scores.append((self._doc_ids[i], score, self._doc_payloads[i]))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k]


async def build_project_bm25_index(
    project_id: str,
    *,
    k1: float = 1.5,
    b: float = 0.75,
) -> BM25 | None:
    """Build an in-memory BM25 index from all chunks in a project."""
    vector_store = get_vector_store()
    embedding_service = get_embedding_service()
    dummy_vector = [0.0] * embedding_service.dimension

    results = vector_store.search(
        query_vector=dummy_vector,
        project_id=project_id,
        top_k=10000,
        score_threshold=0.0,
    )

    if not results:
        logger.warning("No chunks found for BM25 index (project %s)", project_id)
        return None

    documents: list[str] = []
    doc_ids: list[str] = []
    payloads: list[dict[str, Any]] = []

    for result in results:
        payload = result.get("payload", {})
        content = payload.get("content", "")
        if content:
            documents.append(content)
            doc_ids.append(result.get("id", ""))
            payloads.append(payload)

    index = BM25(k1=k1, b=b)
    index.fit(documents, doc_ids, payloads)
    logger.info(
        "Built BM25 index with %d chunks for project %s", len(documents), project_id
    )
    return index
