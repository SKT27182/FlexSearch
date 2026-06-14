"""Index documents into Neo4j knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import NAMESPACE_DNS, uuid5

from app.rag.embedding import get_embedding_service
from app.rag.graph.extractor import GraphExtractor
from app.schemas.rag_config import GraphRagConfig
from app.services.neo4j_store import (
    EntityRecord,
    Neo4jStore,
    PassageRecord,
    RelationRecord,
    get_neo4j_store,
)
from app.utils.logger import create_logger

logger = create_logger(__name__)


@dataclass
class IndexStats:
    passage_count: int
    entity_count: int
    relationship_count: int


class GraphIndexer:
    """Build Neo4j graph from extracted document text."""

    def __init__(self, store: Neo4jStore | None = None) -> None:
        self._store = store or get_neo4j_store()
        self._embedding = get_embedding_service()

    @staticmethod
    def passage_id(document_id: str, chunk_index: int) -> str:
        return str(uuid5(NAMESPACE_DNS, f"{document_id}:passage:{chunk_index}"))

    @staticmethod
    def split_passages(text: str, chunk_size: int) -> list[str]:
        text = text.strip()
        if not text:
            return []
        passages: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end].strip()
            if chunk:
                passages.append(chunk)
            if end >= len(text):
                break
            start = max(end - 50, start + 1)
        return passages

    async def index_document(
        self,
        project_id: str,
        document_id: str,
        filename: str,
        text: str,
        config: GraphRagConfig,
    ) -> IndexStats:
        self._store.ensure_schema()
        self._store.upsert_project(project_id)
        self._store.upsert_document(project_id, document_id, filename)
        self._store.delete_document_subgraph(project_id, document_id)

        passages = self.split_passages(
            text, config.extraction.passage_chunk_size
        )
        extractor = GraphExtractor(
            max_entities=config.indexing.max_entities_per_passage
        )

        entity_ids_for_embed: set[str] = set()
        entity_descriptions: dict[str, str] = {}
        rel_count = 0

        for idx, passage_text in enumerate(passages):
            pid = self.passage_id(document_id, idx)
            self._store.upsert_passage(
                project_id,
                PassageRecord(
                    passage_id=pid,
                    document_id=document_id,
                    content=passage_text,
                    chunk_index=idx,
                    filename=filename,
                ),
            )

            try:
                extracted = await extractor.extract(project_id, passage_text)
            except Exception:
                logger.exception("Graph extraction failed for passage %s", pid)
                continue

            for entity in extracted.entities:
                self._store.upsert_entity(
                    project_id,
                    EntityRecord(
                        entity_id=entity.entity_id,
                        name=entity.name,
                        type=entity.type,
                        description=entity.description,
                    ),
                )
                self._store.link_passage_entity(
                    project_id, pid, entity.entity_id
                )
                entity_ids_for_embed.add(entity.entity_id)
                entity_descriptions[entity.entity_id] = entity.description

            for rel in extracted.relationships:
                self._store.upsert_relation(
                    project_id,
                    RelationRecord(
                        source_entity_id=rel.source_entity_id,
                        target_entity_id=rel.target_entity_id,
                        type=rel.type,
                        description=rel.description,
                    ),
                )
                rel_count += 1

        if config.indexing.embed_entities and entity_ids_for_embed:
            texts = [
                entity_descriptions[eid]
                for eid in sorted(entity_ids_for_embed)
            ]
            ids = sorted(entity_ids_for_embed)
            embeddings = self._embedding.embed_batch(texts)
            self._store.set_entity_embeddings(
                project_id,
                dict(zip(ids, embeddings, strict=True)),
            )

        stats = self._store.get_stats(project_id)
        return IndexStats(
            passage_count=len(passages),
            entity_count=stats.entity_count,
            relationship_count=stats.relationship_count,
        )
