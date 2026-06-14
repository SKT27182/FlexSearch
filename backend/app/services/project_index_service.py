"""Wipe vector or graph indexes when switching rag_mode or deleting projects."""

from __future__ import annotations

from uuid import UUID

from app.services.graphrag_workspace import graphrag_storage_prefix
from app.services.storage import get_storage_service
from app.services.vector_store import get_vector_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


def wipe_vector_index(project_id: UUID | str) -> None:
    get_vector_store().delete_by_project(str(project_id))
    logger.info("Wiped Qdrant vectors for project %s", project_id)


def wipe_graph_workspace(project_id: UUID | str) -> None:
    storage = get_storage_service()
    prefix = graphrag_storage_prefix(project_id)
    storage.delete_prefix(prefix)
    logger.info("Wiped GraphRAG workspace for project %s", project_id)


def wipe_index_for_mode(project_id: UUID | str, *, from_mode: str) -> None:
    if from_mode == "vector":
        wipe_vector_index(project_id)
    elif from_mode == "graph":
        wipe_graph_workspace(project_id)
