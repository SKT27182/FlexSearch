"""Index documents into Neo4j knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass
import math
from uuid import NAMESPACE_DNS, uuid5

from app.services.embedding import get_embedding_service
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
    def passage_id(document_id: str, chunk_index: int, generation: int = 1) -> str:
        return str(
            uuid5(NAMESPACE_DNS, f"{document_id}:g{generation}:passage:{chunk_index}")
        )

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
        generation: int = 1,
    ) -> IndexStats:
        passages = self.split_passages(text, config.extraction.passage_chunk_size)
        extractor = GraphExtractor(
            max_entities=config.indexing.max_entities_per_passage
        )

        entity_ids_for_embed: set[str] = set()
        entity_records: dict[str, EntityRecord] = {}
        entity_descriptions: dict[str, str] = {}
        passage_records: list[PassageRecord] = []
        mentions: list[tuple[str, str]] = []
        relation_records: list[RelationRecord] = []

        for idx, passage_text in enumerate(passages):
            pid = self.passage_id(document_id, idx, generation)
            passage_records.append(
                PassageRecord(
                    passage_id=pid,
                    document_id=document_id,
                    content=passage_text,
                    chunk_index=idx,
                    filename=filename,
                )
            )

            try:
                extracted = await extractor.extract(project_id, passage_text)
            except Exception as exc:
                raise RuntimeError(
                    f"Graph extraction failed for passage {idx + 1}/{len(passages)}: "
                    f"{exc}"
                ) from exc

            for entity in extracted.entities:
                entity_records[entity.entity_id] = EntityRecord(
                    entity_id=entity.entity_id,
                    name=entity.name,
                    type=entity.type,
                    description=entity.description,
                )
                mentions.append((pid, entity.entity_id))
                entity_ids_for_embed.add(entity.entity_id)
                entity_descriptions[entity.entity_id] = entity.description

            for rel in extracted.relationships:
                relation_records.append(
                    RelationRecord(
                        source_entity_id=rel.source_entity_id,
                        target_entity_id=rel.target_entity_id,
                        type=rel.type,
                        description=rel.description,
                    )
                )

        entity_embeddings: dict[str, list[float]] = {}
        if config.indexing.embed_entities and entity_ids_for_embed:
            texts = [entity_descriptions[eid] for eid in sorted(entity_ids_for_embed)]
            ids = sorted(entity_ids_for_embed)
            embeddings = self._embedding.embed_batch(texts)
            if len(embeddings) != len(ids):
                raise ValueError("Entity embedding count mismatch")
            expected_dimension = self._embedding.dimension
            for vector in embeddings:
                if (
                    len(vector) != expected_dimension
                    or not vector
                    or not all(math.isfinite(value) for value in vector)
                ):
                    raise ValueError("Invalid entity embedding vector")
            entity_embeddings = dict(zip(ids, embeddings, strict=True))

        applied = self._store.replace_document_graph(
            project_id=project_id,
            document_id=document_id,
            filename=filename,
            generation=generation,
            passages=passage_records,
            entities=list(entity_records.values()),
            mentions=mentions,
            relations=relation_records,
            embeddings=entity_embeddings,
        )
        if not applied:
            raise RuntimeError("Neo4j generation fence rejected a stale graph write")
        return IndexStats(
            passage_count=len(passages),
            entity_count=len(entity_records),
            relationship_count=len(relation_records),
        )
