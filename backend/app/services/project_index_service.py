"""Wipe vector or graph indexes when switching rag_mode or deleting projects."""

from __future__ import annotations

from uuid import UUID

from app.services.neo4j_store import get_neo4j_store
from app.services.search_store import get_search_store
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__)


def wipe_vector_index(project_id: UUID | str) -> None:
    get_search_store().delete_by_project(str(project_id))
    logger.info("Wiped OpenSearch vectors for project %s", project_id)


def wipe_graph_workspace(project_id: UUID | str) -> None:
    storage = get_storage_service()
    prefix = f"projects/{project_id}/graphrag"
    storage.delete_prefix(prefix)
    logger.info("Wiped GraphRAG workspace for project %s", project_id)


def wipe_neo4j_graph(project_id: UUID | str) -> None:
    get_neo4j_store().delete_project_subgraph(str(project_id))
    logger.info("Wiped Neo4j graph for project %s", project_id)


def wipe_index_for_mode(
    project_id: UUID | str,
    *,
    from_mode: str,
    graph_backend: str | None = None,
) -> None:
    if from_mode == "vector":
        wipe_vector_index(project_id)
    elif from_mode == "graph":
        if graph_backend == "microsoft":
            wipe_graph_workspace(project_id)
        else:
            wipe_neo4j_graph(project_id)
