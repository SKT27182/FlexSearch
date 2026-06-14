"""Graph-global retrieval dispatching to Neo4j or Microsoft GraphRAG backends."""

from __future__ import annotations

from typing import Literal

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.graph_global_microsoft import (
    MicrosoftGraphGlobalRetrieval,
    build_microsoft_graph_global,
)
from app.rag.retrieval.graph_global_neo4j import Neo4jGraphGlobalRetrieval
from app.schemas.rag_config import GraphGlobalRetrievalParams, GraphRetrievalConfig

GraphBackend = Literal["neo4j", "microsoft"]


class GraphGlobalRetrieval(BaseRetrievalStrategy):
    """Dispatches graph_global retrieval to the configured graph backend."""

    def __init__(
        self,
        *,
        graph_backend: GraphBackend,
        microsoft_params: GraphGlobalRetrievalParams | None = None,
        neo4j_config: GraphRetrievalConfig | None = None,
    ) -> None:
        self._graph_backend = graph_backend
        if graph_backend == "neo4j":
            self._delegate: BaseRetrievalStrategy = Neo4jGraphGlobalRetrieval(
                neo4j_config or GraphRetrievalConfig(strategy="graph_global")
            )
        else:
            params = microsoft_params or GraphGlobalRetrievalParams()
            self._delegate = build_microsoft_graph_global(params)

    @property
    def name(self) -> str:
        return "graph_global"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        return await self._delegate.retrieve(query, project_id, top_k=top_k)


def build_graph_global(
    *,
    graph_backend: GraphBackend = "microsoft",
    microsoft_params: GraphGlobalRetrievalParams | None = None,
    neo4j_config: GraphRetrievalConfig | None = None,
) -> GraphGlobalRetrieval:
    return GraphGlobalRetrieval(
        graph_backend=graph_backend,
        microsoft_params=microsoft_params,
        neo4j_config=neo4j_config,
    )
