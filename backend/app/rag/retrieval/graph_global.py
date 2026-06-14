"""Graph-global retrieval: fulltext passage search across project."""

from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.schemas.rag_config import GraphGlobalRetrievalParams, GraphRetrievalConfig
from app.services.neo4j_store import get_neo4j_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class GraphGlobalRetrieval(BaseRetrievalStrategy):
    """Thematic retrieval via passage fulltext search."""

    def __init__(self, config: GraphRetrievalConfig) -> None:
        self._params = config.resolved_params()
        if not isinstance(self._params, GraphGlobalRetrievalParams):
            self._params = GraphGlobalRetrievalParams.model_validate(config.params)
        self._store = get_neo4j_store()

    @property
    def name(self) -> str:
        return "graph_global"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        limit = max(top_k, self._params.top_passages)
        passages = self._store.search_passages_fulltext(
            project_id=project_id,
            query=query,
            limit=limit,
        )

        results: list[RetrievalResult] = []
        for row in passages:
            results.append(
                RetrievalResult(
                    content=row.get("content", ""),
                    score=float(row.get("score", 0.0)),
                    document_id=row.get("document_id", ""),
                    chunk_id=row.get("passage_id", ""),
                    metadata={
                        "filename": row.get("filename", ""),
                        "chunk_index": row.get("chunk_index", 0),
                        "retrieval_type": "graph_global",
                    },
                )
            )

        logger.debug("Graph global retrieval: passages=%d", len(results))
        return results[:top_k]
