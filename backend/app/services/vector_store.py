"""
FlexSearch Backend - Vector Store Service

Compatibility shim: vector RAG now uses OpenSearch via SearchStore.
Prefer `app.services.search_store.get_search_store`.
"""

from app.services.search_store import get_search_store
from app.services.search_store.protocol import SearchStore

# Back-compat alias used by older call sites / tests
get_vector_store = get_search_store

__all__ = ["get_vector_store", "get_search_store", "SearchStore"]
