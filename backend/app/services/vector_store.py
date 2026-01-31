"""
FlexSearch Backend - Vector Store Service

Qdrant vector database abstraction.
"""

from typing import Any
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__)

# Default collection name
COLLECTION_NAME = "flexsearch_chunks"


class VectorStoreService:
    """Qdrant vector store service."""

    def __init__(self) -> None:
        self._client = QdrantClient(url=settings.qdrant_url)
        self._collection = COLLECTION_NAME
        self._vector_size = 384  # all-MiniLM-L6-v2 dimensions

    def ensure_collection(self) -> None:
        """Ensure the collection exists with proper configuration."""
        try:
            collections = self._client.get_collections().collections
            exists = any(c.name == self._collection for c in collections)

            if not exists:
                self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=qdrant_models.VectorParams(
                        size=self._vector_size,
                        distance=qdrant_models.Distance.COSINE,
                    ),
                    hnsw_config=qdrant_models.HnswConfigDiff(
                        m=settings.qdrant_hnsw_m,
                        ef_construct=settings.qdrant_hnsw_ef,
                    ),
                )

                # Create payload index for project_id filtering
                self._client.create_payload_index(
                    collection_name=self._collection,
                    field_name="project_id",
                    field_schema=qdrant_models.PayloadSchemaType.KEYWORD,
                )

                logger.info(f"Created collection: {self._collection}")
        except UnexpectedResponse as e:
            logger.error(f"Failed to ensure collection: {e}")
            raise

    def upsert_vectors(
        self,
        ids: list[str],
        vectors: list[list[float]],
        payloads: list[dict[str, Any]],
    ) -> None:
        """
        Upsert vectors with payloads.

        Args:
            ids: List of point IDs
            vectors: List of embedding vectors
            payloads: List of metadata payloads (must include project_id)
        """
        points = [
            qdrant_models.PointStruct(
                id=point_id,
                vector=vector,
                payload=payload,
            )
            for point_id, vector, payload in zip(ids, vectors, payloads)
        ]

        self._client.upsert(
            collection_name=self._collection,
            points=points,
        )

        logger.info(f"Upserted {len(points)} vectors")

    def search(
        self,
        query_vector: list[float],
        project_id: str,
        top_k: int = 5,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search for similar vectors filtered by project.

        Args:
            query_vector: Query embedding
            project_id: Filter by project
            top_k: Number of results
            score_threshold: Minimum similarity score

        Returns:
            List of search results with payload and score
        """
        results = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="project_id",
                        match=qdrant_models.MatchValue(value=project_id),
                    )
                ]
            ),
            limit=top_k,
            score_threshold=score_threshold,
        )

        return [
            {
                "id": str(result.id),
                "score": result.score,
                "payload": result.payload,
            }
            for result in results.points
        ]

    def search_with_filter(
        self,
        query_vector: list[float],
        filters: dict[str, Any],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search with custom filters."""
        conditions = [
            qdrant_models.FieldCondition(
                key=key,
                match=qdrant_models.MatchValue(value=value),
            )
            for key, value in filters.items()
        ]

        results = self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            query_filter=qdrant_models.Filter(must=conditions),
            limit=top_k,
        )

        return [
            {
                "id": str(result.id),
                "score": result.score,
                "payload": result.payload,
            }
            for result in results.points
        ]

    def delete_by_project(self, project_id: str) -> None:
        """Delete all vectors for a project."""
        self._client.delete(
            collection_name=self._collection,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="project_id",
                            match=qdrant_models.MatchValue(value=project_id),
                        )
                    ]
                )
            ),
        )
        logger.info(f"Deleted vectors for project: {project_id}")

    def delete_by_document(self, document_id: str) -> None:
        """Delete all vectors for a document."""
        self._client.delete(
            collection_name=self._collection,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(
                    must=[
                        qdrant_models.FieldCondition(
                            key="document_id",
                            match=qdrant_models.MatchValue(value=document_id),
                        )
                    ]
                )
            ),
        )
        logger.info(f"Deleted vectors for document: {document_id}")

    def get_collection_info(self) -> dict[str, Any]:
        """Get collection statistics."""
        info = self._client.get_collection(self._collection)
        return {
            "vectors_count": info.vectors_count,
            "points_count": info.points_count,
            "status": info.status.value,
        }


# Singleton instance
_vector_store: VectorStoreService | None = None


def get_vector_store() -> VectorStoreService:
    """Get vector store singleton."""
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStoreService()
        _vector_store.ensure_collection()
    return _vector_store
