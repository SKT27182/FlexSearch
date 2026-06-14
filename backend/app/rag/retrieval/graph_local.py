"""Graph-local retrieval dispatching to Neo4j or Microsoft GraphRAG backends."""

from __future__ import annotations

from typing import Literal

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.graph_local_microsoft import (
    MicrosoftGraphLocalRetrieval,
    build_microsoft_graph_local,
)
from app.rag.retrieval.graph_local_neo4j import Neo4jGraphLocalRetrieval
from app.schemas.rag_config import (
    GraphLocalRetrievalParams,
    GraphRetrievalConfig,
    MicrosoftGraphLocalRetrievalParams,
)

GraphBackend = Literal["neo4j", "microsoft"]


class GraphLocalRetrieval(BaseRetrievalStrategy):
    """Dispatches graph_local retrieval to the configured graph backend."""

    def __init__(
        self,
        *,
        graph_backend: GraphBackend,
        microsoft_params: MicrosoftGraphLocalRetrievalParams | None = None,
        neo4j_config: GraphRetrievalConfig | None = None,
    ) -> None:
        self._graph_backend = graph_backend
        if graph_backend == "neo4j":
            self._delegate: BaseRetrievalStrategy = Neo4jGraphLocalRetrieval(
                neo4j_config or GraphRetrievalConfig(strategy="graph_local")
            )
        else:
            params = microsoft_params or MicrosoftGraphLocalRetrievalParams()
            self._delegate = build_microsoft_graph_local(params)

    @property
    def name(self) -> str:
        return "graph_local"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        return await self._delegate.retrieve(query, project_id, top_k=top_k)


def build_graph_local(
    *,
    graph_backend: GraphBackend = "microsoft",
    microsoft_params: MicrosoftGraphLocalRetrievalParams | None = None,
    neo4j_config: GraphRetrievalConfig | None = None,
) -> GraphLocalRetrieval:
    return GraphLocalRetrieval(
        graph_backend=graph_backend,
        microsoft_params=microsoft_params,
        neo4j_config=neo4j_config,
    )
