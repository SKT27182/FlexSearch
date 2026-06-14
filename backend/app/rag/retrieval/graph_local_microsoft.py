"""Microsoft GraphRAG local search retrieval strategy."""

from __future__ import annotations

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.rag.retrieval.graph_context_mapper import context_to_retrieval_results
from app.schemas.rag_config import GraphLocalRetrievalParams
from app.services.graphrag_workspace import get_graphrag_workspace


class MicrosoftGraphLocalRetrieval(BaseRetrievalStrategy):
    """Entity-focused GraphRAG local search."""

    def __init__(self, *, community_level: int = 2) -> None:
        self._community_level = community_level

    @property
    def name(self) -> str:
        return "graph_local"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        workspace = get_graphrag_workspace()
        context = await workspace.run_local_search(
            project_id,
            query,
            community_level=self._community_level,
            top_k=top_k,
        )
        return context_to_retrieval_results(context, top_k=top_k)


def build_microsoft_graph_local(
    params: GraphLocalRetrievalParams,
) -> MicrosoftGraphLocalRetrieval:
    return MicrosoftGraphLocalRetrieval(community_level=params.community_level)
