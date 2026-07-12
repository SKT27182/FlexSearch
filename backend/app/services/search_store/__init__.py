"""SearchStore factory / singleton."""

from __future__ import annotations

from app.services.search_store.opensearch_store import OpenSearchStore
from app.services.search_store.protocol import SearchStore
from app.utils.logger import create_logger

logger = create_logger(__name__)

_search_store: OpenSearchStore | None = None


def get_search_store() -> SearchStore:
    """Return the process-wide OpenSearch SearchStore singleton."""
    global _search_store
    if _search_store is None:
        _search_store = OpenSearchStore()
        try:
            _search_store.ensure_index()
        except Exception as exc:
            # Allow API boot when OpenSearch is temporarily down; first
            # upsert/search will retry ensure_index.
            logger.warning("OpenSearch ensure_index deferred: %s", exc)
    return _search_store


def reset_search_store() -> None:
    """Close and clear the singleton (tests)."""
    global _search_store
    if _search_store is not None:
        _search_store.close()
        _search_store = None
