"""SearchStore protocol — vector + BM25 + hybrid retrieval abstraction."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.search_store.types import SearchDocument, SearchFilters, SearchHit


@runtime_checkable
class SearchStore(Protocol):
    """Backend-agnostic search index used by vector RAG retrieval."""

    def ensure_index(self, dimension: int | None = None) -> None:
        """Create the index if missing; fail fast on dimension mismatch."""
        ...

    def upsert(self, documents: list[SearchDocument]) -> None:
        """Bulk upsert documents (embeddings + payload)."""
        ...

    def dense_search(
        self,
        query_vector: list[float],
        filters: SearchFilters,
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list[SearchHit]:
        """k-NN / dense vector search."""
        ...

    def bm25_search(
        self,
        query: str,
        filters: SearchFilters,
        top_k: int = 5,
    ) -> list[SearchHit]:
        """Lexical BM25 search over `content`."""
        ...

    def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        filters: SearchFilters,
        top_k: int = 5,
        rrf_k: int = 60,
    ) -> list[SearchHit]:
        """Dense + BM25 fused with Reciprocal Rank Fusion."""
        ...

    def get_by_ids(self, ids: list[str]) -> list[SearchHit]:
        """Fetch documents by id (order not guaranteed)."""
        ...

    def delete_by_document(self, document_id: str) -> None:
        """Delete all docs for a document_id."""
        ...

    def delete_by_project(self, project_id: str) -> None:
        """Delete all docs for a project_id."""
        ...

    def delete_by_ids(self, ids: list[str]) -> None:
        """Delete documents by id."""
        ...

    def scroll(
        self,
        filters: SearchFilters,
        *,
        size: int = 100,
        search_after: list | None = None,
    ) -> tuple[list[SearchHit], list | None]:
        """Paginated scan; returns (hits, next_search_after)."""
        ...

    def get_index_info(self) -> dict:
        """Index / cluster stats for health and admin."""
        ...
