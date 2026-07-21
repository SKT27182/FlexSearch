"""OpenSearch-backed SearchStore (knn + BM25 + hybrid RRF)."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from opensearchpy import OpenSearch, RequestsHttpConnection, helpers
from opensearchpy.exceptions import NotFoundError, RequestError

from app.core.config import settings
from app.services.search_store.types import SearchDocument, SearchFilters, SearchHit
from app.utils.logger import create_logger

logger = create_logger(__name__)


class OpenSearchStoreError(Exception):
    """OpenSearch search-store operation failed."""


class OpenSearchStore:
    """FlexSearch vector/hybrid index on OpenSearch."""

    def __init__(
        self,
        *,
        url: str | None = None,
        index_name: str | None = None,
        dimension: int | None = None,
    ) -> None:
        self._url = url or settings.opensearch_url
        self._index = index_name or settings.opensearch_index
        self._dimension = dimension
        self._client: OpenSearch | None = None

    @property
    def index_name(self) -> str:
        return self._index

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            from app.services.embedding import get_embedding_service

            self._dimension = get_embedding_service().dimension
        return self._dimension

    def _get_client(self) -> OpenSearch:
        if self._client is None:
            parsed = urlparse(self._url)
            host = parsed.hostname or "localhost"
            port = parsed.port or (443 if parsed.scheme == "https" else 9200)
            use_ssl = parsed.scheme == "https" or settings.opensearch_use_ssl
            http_auth = None
            if settings.opensearch_username:
                http_auth = (
                    settings.opensearch_username,
                    settings.opensearch_password,
                )
            self._client = OpenSearch(
                hosts=[{"host": host, "port": port}],
                http_auth=http_auth,
                use_ssl=use_ssl,
                verify_certs=settings.opensearch_verify_certs,
                ssl_show_warn=False,
                connection_class=RequestsHttpConnection,
                timeout=30,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def ping(self) -> bool:
        try:
            return bool(self._get_client().ping())
        except Exception:
            return False

    def _index_body(self, dimension: int) -> dict[str, Any]:
        return {
            "settings": {
                "index": {
                    "knn": True,
                    "knn.algo_param.ef_search": settings.opensearch_knn_ef_search,
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                }
            },
            "mappings": {
                "properties": {
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": dimension,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "lucene",
                            "parameters": {
                                "ef_construction": settings.opensearch_knn_ef_construction,
                                "m": settings.opensearch_knn_m,
                            },
                        },
                    },
                    "content": {"type": "text"},
                    "project_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "rag_generation": {"type": "integer"},
                    "embedding_model": {"type": "keyword"},
                    "embedding_dimension": {"type": "integer"},
                    "chunk_index": {"type": "integer"},
                    "chunk_type": {"type": "keyword"},
                    "parent_id": {"type": "keyword"},
                    "summary_level": {"type": "keyword"},
                    "cluster_id": {"type": "keyword"},
                    "member_chunk_ids": {"type": "keyword"},
                    "filename": {"type": "keyword"},
                    "start_char": {"type": "integer"},
                    "end_char": {"type": "integer"},
                }
            },
        }

    def ensure_index(self, dimension: int | None = None) -> None:
        dim = dimension or self.dimension
        self._dimension = dim
        client = self._get_client()
        try:
            exists = client.indices.exists(index=self._index)
        except Exception as exc:
            raise OpenSearchStoreError(
                f"OpenSearch unavailable at {self._url}: {exc}"
            ) from exc

        if not exists:
            try:
                client.indices.create(index=self._index, body=self._index_body(dim))
                logger.info(
                    "Created OpenSearch index %s (dimension=%d)", self._index, dim
                )
            except RequestError as exc:
                if "resource_already_exists_exception" not in str(exc):
                    raise OpenSearchStoreError(
                        f"Failed to create index {self._index}: {exc}"
                    ) from exc
            return

        # Fail fast on dimension mismatch
        mapping = client.indices.get_mapping(index=self._index)
        props = mapping.get(self._index, {}).get("mappings", {}).get("properties", {})
        emb = props.get("embedding") or {}
        existing_dim = emb.get("dimension")
        if existing_dim is not None and int(existing_dim) != int(dim):
            raise OpenSearchStoreError(
                f"OpenSearch index {self._index} has embedding dimension "
                f"{existing_dim}, but embedding model expects {dim}. "
                "Delete/recreate the index or change EMBEDDING_MODEL."
            )
        if "summary_level" not in props:
            logger.warning(
                "Index %s missing summary_level mapping; greenfield expects it. "
                "Recreate the index for hierarchical summaries.",
                self._index,
            )

    def _filter_clause(self, filters: SearchFilters) -> list[dict[str, Any]]:
        clauses: list[dict[str, Any]] = []
        mapping = {
            "project_id": filters.project_id,
            "document_id": filters.document_id,
            "rag_generation": filters.rag_generation,
            "chunk_type": filters.chunk_type,
            "parent_id": filters.parent_id,
            "cluster_id": filters.cluster_id,
        }
        for field, value in mapping.items():
            if value is not None:
                clauses.append({"term": {field: value}})
        if filters.summary_levels:
            clauses.append({"terms": {"summary_level": list(filters.summary_levels)}})
        elif filters.summary_level is not None:
            clauses.append({"term": {"summary_level": filters.summary_level}})
        if filters.chunk_index_min is not None or filters.chunk_index_max is not None:
            range_body: dict[str, Any] = {}
            if filters.chunk_index_min is not None:
                range_body["gte"] = filters.chunk_index_min
            if filters.chunk_index_max is not None:
                range_body["lte"] = filters.chunk_index_max
            clauses.append({"range": {"chunk_index": range_body}})
        return clauses

    def _wrap_query(
        self, query: dict[str, Any], filters: SearchFilters
    ) -> dict[str, Any]:
        clauses = self._filter_clause(filters)
        if not clauses:
            return query
        return {
            "bool": {
                "must": [query],
                "filter": clauses,
            }
        }

    def upsert(self, documents: list[SearchDocument]) -> int:
        if not documents:
            return 0
        self.ensure_index(len(documents[0].embedding))
        actions = [
            {
                "_op_type": "index",
                "_index": self._index,
                "_id": doc.id,
                "_source": doc.to_source(),
            }
            for doc in documents
        ]
        success, errors = helpers.bulk(
            self._get_client(),
            actions,
            raise_on_error=False,
            refresh="wait_for",
        )
        if errors:
            logger.error("OpenSearch bulk upsert errors: %s", errors[:3])
            raise OpenSearchStoreError(f"Bulk upsert failed ({len(errors)} errors)")
        if success != len(documents):
            raise OpenSearchStoreError(
                f"Bulk upsert acknowledged {success} of {len(documents)} documents"
            )
        logger.info("Upserted %d documents into %s", success, self._index)
        return success

    def dense_search(
        self,
        query_vector: list[float],
        filters: SearchFilters,
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list[SearchHit]:
        self.ensure_index(len(query_vector))
        knn_query: dict[str, Any] = {
            "knn": {
                "embedding": {
                    "vector": query_vector,
                    "k": top_k,
                }
            }
        }
        # Apply filters via bool filter around knn when present
        clauses = self._filter_clause(filters)
        if clauses:
            body = {
                "size": top_k,
                "query": {
                    "bool": {
                        "must": [knn_query],
                        "filter": clauses,
                    }
                },
            }
        else:
            body = {"size": top_k, "query": knn_query}

        response = self._get_client().search(index=self._index, body=body)
        hits = [SearchHit.from_opensearch(h) for h in response["hits"]["hits"]]
        if score_threshold is not None:
            hits = [h for h in hits if h.score >= score_threshold]
        return hits

    def bm25_search(
        self,
        query: str,
        filters: SearchFilters,
        top_k: int = 5,
    ) -> list[SearchHit]:
        self.ensure_index()
        match_query = {"match": {"content": {"query": query}}}
        body = {
            "size": top_k,
            "query": self._wrap_query(match_query, filters),
        }
        response = self._get_client().search(index=self._index, body=body)
        return [SearchHit.from_opensearch(h) for h in response["hits"]["hits"]]

    def hybrid_search(
        self,
        query: str,
        query_vector: list[float],
        filters: SearchFilters,
        top_k: int = 5,
        rrf_k: int = 60,
    ) -> list[SearchHit]:
        fetch_k = max(top_k * 3, top_k)
        dense = self.dense_search(query_vector, filters, top_k=fetch_k)
        sparse = self.bm25_search(query, filters, top_k=fetch_k)
        if not sparse:
            return dense[:top_k]
        if not dense:
            return sparse[:top_k]
        return self._rrf([dense, sparse], k=rrf_k)[:top_k]

    @staticmethod
    def _rrf(result_lists: list[list[SearchHit]], k: int = 60) -> list[SearchHit]:
        scores: dict[str, float] = {}
        hit_map: dict[str, SearchHit] = {}
        for results in result_lists:
            for rank, hit in enumerate(results):
                scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank + 1)
                existing = hit_map.get(hit.id)
                if existing is None or hit.score > existing.score:
                    hit_map[hit.id] = hit
        ordered = sorted(scores.keys(), key=lambda i: scores[i], reverse=True)
        fused: list[SearchHit] = []
        for doc_id in ordered:
            hit = hit_map[doc_id].model_copy(deep=True)
            hit.payload = {**hit.payload, "rrf_score": scores[doc_id]}
            hit.score = scores[doc_id]
            fused.append(hit)
        return fused

    def get_by_ids(self, ids: list[str]) -> list[SearchHit]:
        if not ids:
            return []
        self.ensure_index()
        unique_ids = list(dict.fromkeys(ids))
        try:
            response = self._get_client().mget(
                index=self._index, body={"ids": unique_ids}
            )
        except NotFoundError:
            return []
        hits: list[SearchHit] = []
        for doc in response.get("docs", []):
            if not doc.get("found"):
                continue
            hits.append(
                SearchHit.from_opensearch(
                    {
                        "_id": doc.get("_id"),
                        "_score": 0.0,
                        "_source": doc.get("_source") or {},
                    }
                )
            )
        return hits

    def delete_by_document(self, document_id: str) -> None:
        self._delete_by_term("document_id", document_id)

    def delete_by_project(self, project_id: str) -> None:
        self._delete_by_term("project_id", project_id)

    def delete_old_project_generations(
        self, project_id: str, keep_generation: int
    ) -> None:
        client = self._get_client()
        if not client.indices.exists(index=self._index):
            return
        client.delete_by_query(
            index=self._index,
            body={
                "query": {
                    "bool": {
                        "filter": [{"term": {"project_id": project_id}}],
                        "must_not": [{"term": {"rag_generation": keep_generation}}],
                    }
                }
            },
            refresh=True,
            conflicts="proceed",
        )
        logger.info(
            "Deleted stale OpenSearch generations for project=%s keep=%s",
            project_id,
            keep_generation,
        )

    def delete_by_ids(self, ids: list[str]) -> None:
        """Delete documents by id (used to refresh summaries without wiping chunks)."""
        if not ids:
            return
        client = self._get_client()
        if not client.indices.exists(index=self._index):
            return
        actions = [
            {"_op_type": "delete", "_index": self._index, "_id": doc_id}
            for doc_id in ids
        ]
        helpers.bulk(
            client,
            actions,
            raise_on_error=False,
            refresh="wait_for",
        )
        logger.info("Deleted %d OpenSearch docs by id", len(ids))

    def _delete_by_term(self, field: str, value: str) -> None:
        client = self._get_client()
        if not client.indices.exists(index=self._index):
            return
        client.delete_by_query(
            index=self._index,
            body={"query": {"term": {field: value}}},
            refresh=True,
            conflicts="proceed",
        )
        logger.info("Deleted OpenSearch docs where %s=%s", field, value)

    def scroll(
        self,
        filters: SearchFilters,
        *,
        size: int = 100,
        search_after: list | None = None,
    ) -> tuple[list[SearchHit], list | None]:
        self.ensure_index()
        clauses = self._filter_clause(filters)
        query: dict[str, Any] = (
            {"bool": {"filter": clauses}} if clauses else {"match_all": {}}
        )
        body: dict[str, Any] = {
            "size": size,
            "query": query,
            "sort": [{"_id": "asc"}],
        }
        if search_after is not None:
            body["search_after"] = search_after
        response = self._get_client().search(index=self._index, body=body)
        raw_hits = response["hits"]["hits"]
        hits = [SearchHit.from_opensearch(h) for h in raw_hits]
        next_after = raw_hits[-1]["sort"] if raw_hits else None
        return hits, next_after

    def get_index_info(self) -> dict[str, Any]:
        client = self._get_client()
        if not client.indices.exists(index=self._index):
            return {"index": self._index, "exists": False}
        stats = client.indices.stats(index=self._index)
        idx = stats.get("indices", {}).get(self._index, {})
        primaries = idx.get("primaries", {})
        docs = primaries.get("docs", {})
        return {
            "index": self._index,
            "exists": True,
            "docs_count": docs.get("count", 0),
            "store_size_bytes": primaries.get("store", {}).get("size_in_bytes"),
            "dimension": self._dimension,
        }
