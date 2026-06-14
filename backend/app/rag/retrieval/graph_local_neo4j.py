"""Graph-local retrieval: entity match + hop expansion."""

from app.rag.embedding import get_embedding_service
from app.rag.retrieval.base import BaseRetrievalStrategy, RetrievalResult
from app.schemas.rag_config import GraphLocalRetrievalParams, GraphRetrievalConfig
from app.services.neo4j_store import get_neo4j_store
from app.utils.logger import create_logger

logger = create_logger(__name__)


class Neo4jGraphLocalRetrieval(BaseRetrievalStrategy):
    """Entity-centric graph retrieval with relationship expansion."""

    def __init__(self, config: GraphRetrievalConfig) -> None:
        self._params = config.resolved_params()
        if not isinstance(self._params, GraphLocalRetrievalParams):
            self._params = GraphLocalRetrievalParams.model_validate(config.params)
        self._store = get_neo4j_store()
        self._embedding = get_embedding_service()

    @property
    def name(self) -> str:
        return "graph_local"

    async def retrieve(
        self,
        query: str,
        project_id: str,
        top_k: int = 5,
    ) -> list[RetrievalResult]:
        embedding = self._embedding.embed(query)
        entities = self._store.search_entities_for_query(
            project_id=project_id,
            query=query,
            embedding=embedding,
            top_k=self._params.top_entities,
        )
        entity_ids = [e["entity_id"] for e in entities if e.get("entity_id")]
        passages = self._store.get_passages_for_entities(
            project_id=project_id,
            entity_ids=entity_ids,
            max_hops=self._params.max_hops,
            limit=top_k * 3,
        )

        entity_scores = {
            e["entity_id"]: float(e.get("score", 0.0)) for e in entities
        }
        results: list[RetrievalResult] = []
        seen: set[str] = set()
        for row in passages:
            pid = row.get("passage_id", "")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            score = float(row.get("score", 0.0))
            results.append(
                RetrievalResult(
                    content=row.get("content", ""),
                    score=score,
                    document_id=row.get("document_id", ""),
                    chunk_id=pid,
                    metadata={
                        "filename": row.get("filename", ""),
                        "chunk_index": row.get("chunk_index", 0),
                        "entity_name": row.get("entity_name", ""),
                        "retrieval_type": "graph_local",
                    },
                )
            )
            if len(results) >= top_k:
                break

        if not results and entities:
            for ent in entities[:top_k]:
                results.append(
                    RetrievalResult(
                        content=ent.get("description") or ent.get("name", ""),
                        score=float(ent.get("score", 0.0)),
                        document_id="",
                        chunk_id=ent.get("entity_id", ""),
                        metadata={
                            "entity_name": ent.get("name", ""),
                            "retrieval_type": "graph_local",
                            "source": "entity",
                        },
                    )
                )

        logger.debug(
            "Graph local retrieval: entities=%d passages=%d",
            len(entities),
            len(results),
        )
        return results[:top_k]
