"""GraphRAG global search retrieval strategy."""

from __future__ import annotations

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.graph_context_mapper import context_to_retrieval_results
from app.schemas.rag_config import GraphGlobalRetrievalParams
from app.services.graphrag_workspace import get_graphrag_workspace


class GraphGlobalRetrieval(BaseRetrievalStrategy):
    """Thematic GraphRAG global search using community reports."""

    def __init__(
        self,
        *,
        community_level: int = 2,
        dynamic_community_selection: bool = False,
    ) -> None:
        self._community_level = community_level
        self._dynamic_community_selection = dynamic_community_selection

    @property
    def name(self) -> str:
        return "graph_global"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        workspace = get_graphrag_workspace()
        context = await workspace.run_global_search(
            project_id,
            query,
            community_level=self._community_level,
            dynamic_community_selection=self._dynamic_community_selection,
            top_k=top_k,
        )
        return context_to_retrieval_results(context, top_k=top_k)


def build_graph_global(params: GraphGlobalRetrievalParams) -> GraphGlobalRetrieval:
    return GraphGlobalRetrieval(
        community_level=params.community_level,
        dynamic_community_selection=params.dynamic_community_selection,
    )
