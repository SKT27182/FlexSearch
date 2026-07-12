"""Neo4j graph store for Graph RAG projects."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo4j import GraphDatabase, Driver
from neo4j.exceptions import Neo4jError, ServiceUnavailable

from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__)


def _embedding_dimension() -> int:
    """Resolve vector dimension from the active embedding model."""
    from app.services.embedding import get_embedding_service

    return get_embedding_service().dimension


class Neo4jStoreError(Exception):
    """Neo4j operation failed."""


@dataclass
class PassageRecord:
    passage_id: str
    document_id: str
    content: str
    chunk_index: int
    filename: str


@dataclass
class EntityRecord:
    entity_id: str
    name: str
    type: str
    description: str


@dataclass
class RelationRecord:
    source_entity_id: str
    target_entity_id: str
    type: str
    description: str


@dataclass
class GraphStats:
    entity_count: int
    passage_count: int
    relationship_count: int


class Neo4jStore:
    """Project-scoped knowledge graph in Neo4j."""

    def __init__(self) -> None:
        self._driver: Driver | None = None

    def _get_driver(self) -> Driver:
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password),
            )
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def verify_connectivity(self) -> None:
        try:
            self._get_driver().verify_connectivity()
        except ServiceUnavailable as exc:
            raise Neo4jStoreError(
                f"Neo4j unavailable at {settings.neo4j_uri}: {exc}"
            ) from exc

    def ensure_schema(self) -> None:
        dim = _embedding_dimension()
        statements = [
            """
            CREATE CONSTRAINT project_id_unique IF NOT EXISTS
            FOR (p:Project) REQUIRE p.project_id IS UNIQUE
            """,
            """
            CREATE CONSTRAINT passage_id_unique IF NOT EXISTS
            FOR (p:Passage) REQUIRE p.passage_id IS UNIQUE
            """,
            """
            CREATE CONSTRAINT entity_id_unique IF NOT EXISTS
            FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE
            """,
            """
            CREATE CONSTRAINT document_id_unique IF NOT EXISTS
            FOR (d:Document) REQUIRE d.document_id IS UNIQUE
            """,
            """
            CREATE FULLTEXT INDEX passage_content IF NOT EXISTS
            FOR (p:Passage) ON EACH [p.content]
            """,
            """
            CREATE FULLTEXT INDEX entity_search IF NOT EXISTS
            FOR (e:Entity) ON EACH [e.name, e.description]
            """,
        ]
        with self._get_driver().session() as session:
            for stmt in statements:
                try:
                    session.run(stmt.strip())
                except Neo4jError as exc:
                    if "EquivalentSchemaRuleAlreadyExists" in str(exc):
                        continue
                    if "An equivalent index already exists" in str(exc):
                        continue
                    logger.warning("Neo4j schema statement skipped: %s", exc)
            self._ensure_entity_vector_index(session, dim)

    def _vector_index_dimensions(self, session: Any, index_name: str) -> int | None:
        """Return configured vector.dimensions for an index, or None if missing."""
        try:
            record = session.run(
                """
                SHOW INDEXES
                YIELD name, type, options
                WHERE name = $name AND type = 'VECTOR'
                RETURN options
                """,
                name=index_name,
            ).single()
        except Neo4jError as exc:
            logger.debug("SHOW INDEXES failed for %s: %s", index_name, exc)
            return None
        if not record:
            return None
        options = record.get("options") or {}
        if not isinstance(options, dict):
            return None
        index_config = options.get("indexConfig") or options.get("index_config") or {}
        if not isinstance(index_config, dict):
            return None
        raw = index_config.get("vector.dimensions")
        if raw is None:
            raw = index_config.get("`vector.dimensions`")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def _ensure_entity_vector_index(self, session: Any, dim: int) -> None:
        """Create or recreate entity_embedding when dimension mismatches.

        IF NOT EXISTS alone leaves a stale index after embedding model changes.
        On mismatch we drop the index, clear stored embeddings (wrong width),
        and recreate — callers must re-run graph indexing to refill vectors.
        """
        index_name = "entity_embedding"
        existing_dim = self._vector_index_dimensions(session, index_name)
        if existing_dim == dim:
            return

        if existing_dim is not None:
            logger.warning(
                "Neo4j vector index %s dimension mismatch (%s -> %s); "
                "recreating index and clearing entity embeddings. "
                "Re-run graph indexing to restore vectors.",
                index_name,
                existing_dim,
                dim,
            )
            try:
                session.run(f"DROP INDEX {index_name} IF EXISTS")
            except Neo4jError as exc:
                raise Neo4jStoreError(
                    f"Failed to drop stale vector index {index_name} "
                    f"(dim {existing_dim} -> {dim}): {exc}"
                ) from exc
            try:
                session.run(
                    """
                    MATCH (e:Entity)
                    WHERE e.embedding IS NOT NULL
                    SET e.embedding = null
                    """
                )
            except Neo4jError as exc:
                logger.warning(
                    "Could not clear stale entity embeddings after dimension change: %s",
                    exc,
                )

        create_stmt = f"""
            CREATE VECTOR INDEX {index_name} IF NOT EXISTS
            FOR (e:Entity) ON (e.embedding)
            OPTIONS {{indexConfig: {{
              `vector.dimensions`: {dim},
              `vector.similarity_function`: 'cosine'
            }}}}
            """
        try:
            session.run(create_stmt.strip())
        except Neo4jError as exc:
            if "EquivalentSchemaRuleAlreadyExists" in str(exc):
                return
            if "An equivalent index already exists" in str(exc):
                # Exists with unknown/other config — fail clearly rather than
                # silently querying with the wrong dimension.
                raise Neo4jStoreError(
                    f"Vector index {index_name} already exists but dimension "
                    f"could not be verified against embedding dim={dim}. "
                    f"Drop the index manually or fix Neo4j SHOW INDEXES access: {exc}"
                ) from exc
            raise Neo4jStoreError(
                f"Failed to create vector index {index_name} (dim={dim}): {exc}"
            ) from exc

    def upsert_project(self, project_id: str, name: str = "") -> None:
        with self._get_driver().session() as session:
            session.run(
                """
                MERGE (p:Project {project_id: $project_id})
                SET p.name = $name
                """,
                project_id=project_id,
                name=name,
            )

    def upsert_document(
        self,
        project_id: str,
        document_id: str,
        filename: str,
    ) -> None:
        with self._get_driver().session() as session:
            session.run(
                """
                MERGE (proj:Project {project_id: $project_id})
                MERGE (d:Document {document_id: $document_id})
                SET d.filename = $filename, d.project_id = $project_id
                MERGE (d)-[:IN_PROJECT]->(proj)
                """,
                project_id=project_id,
                document_id=document_id,
                filename=filename,
            )

    def upsert_passage(
        self,
        project_id: str,
        passage: PassageRecord,
    ) -> None:
        with self._get_driver().session() as session:
            session.run(
                """
                MATCH (d:Document {document_id: $document_id})
                MERGE (p:Passage {passage_id: $passage_id})
                SET p.content = $content,
                    p.chunk_index = $chunk_index,
                    p.filename = $filename,
                    p.document_id = $document_id,
                    p.project_id = $project_id
                MERGE (p)-[:FROM_DOCUMENT]->(d)
                """,
                project_id=project_id,
                document_id=passage.document_id,
                passage_id=passage.passage_id,
                content=passage.content,
                chunk_index=passage.chunk_index,
                filename=passage.filename,
            )

    def upsert_entity(
        self,
        project_id: str,
        entity: EntityRecord,
    ) -> None:
        with self._get_driver().session() as session:
            session.run(
                """
                MERGE (e:Entity {entity_id: $entity_id})
                SET e.name = $name,
                    e.type = $type,
                    e.description = $description,
                    e.project_id = $project_id
                """,
                project_id=project_id,
                entity_id=entity.entity_id,
                name=entity.name,
                type=entity.type,
                description=entity.description,
            )

    def link_passage_entity(
        self,
        project_id: str,
        passage_id: str,
        entity_id: str,
    ) -> None:
        with self._get_driver().session() as session:
            # OPTIONAL MATCH: if a concurrent delete wiped the passage/entity,
            # no-op instead of Neo4j EntityNotFound on stale element ids.
            session.run(
                """
                OPTIONAL MATCH (p:Passage {passage_id: $passage_id, project_id: $project_id})
                OPTIONAL MATCH (e:Entity {entity_id: $entity_id, project_id: $project_id})
                WITH p, e
                WHERE p IS NOT NULL AND e IS NOT NULL
                MERGE (p)-[:MENTIONS]->(e)
                """,
                project_id=project_id,
                passage_id=passage_id,
                entity_id=entity_id,
            )

    def upsert_relation(
        self,
        project_id: str,
        relation: RelationRecord,
    ) -> None:
        with self._get_driver().session() as session:
            session.run(
                """
                OPTIONAL MATCH (a:Entity {entity_id: $source_id, project_id: $project_id})
                OPTIONAL MATCH (b:Entity {entity_id: $target_id, project_id: $project_id})
                WITH a, b
                WHERE a IS NOT NULL AND b IS NOT NULL
                MERGE (a)-[r:RELATES_TO {type: $rel_type}]->(b)
                SET r.description = $description
                """,
                project_id=project_id,
                source_id=relation.source_entity_id,
                target_id=relation.target_entity_id,
                rel_type=relation.type,
                description=relation.description,
            )

    def set_entity_embeddings(
        self,
        project_id: str,
        entity_embeddings: dict[str, list[float]],
    ) -> None:
        if not entity_embeddings:
            return
        with self._get_driver().session() as session:
            for entity_id, embedding in entity_embeddings.items():
                session.run(
                    """
                    MATCH (e:Entity {entity_id: $entity_id, project_id: $project_id})
                    SET e.embedding = $embedding
                    """,
                    project_id=project_id,
                    entity_id=entity_id,
                    embedding=embedding,
                )

    def delete_document_subgraph(self, project_id: str, document_id: str) -> None:
        with self._get_driver().session() as session:
            session.run(
                """
                MATCH (d:Document {document_id: $document_id, project_id: $project_id})
                OPTIONAL MATCH (d)<-[:FROM_DOCUMENT]-(p:Passage)
                OPTIONAL MATCH (p)-[:MENTIONS]->(e:Entity)
                WITH collect(DISTINCT p) AS passages, collect(DISTINCT e) AS entities, d
                FOREACH (n IN passages | DETACH DELETE n)
                WITH entities, d
                UNWIND entities AS ent
                WITH ent, d
                WHERE NOT ()-[:MENTIONS]->(ent)
                DETACH DELETE ent
                DETACH DELETE d
                """,
                project_id=project_id,
                document_id=document_id,
            )

    def delete_project_subgraph(self, project_id: str) -> None:
        with self._get_driver().session() as session:
            session.run(
                """
                MATCH (n {project_id: $project_id})
                DETACH DELETE n
                """,
                project_id=project_id,
            )
            session.run(
                """
                MATCH (p:Project {project_id: $project_id})
                DETACH DELETE p
                """,
                project_id=project_id,
            )

    def get_stats(self, project_id: str) -> GraphStats:
        with self._get_driver().session() as session:
            record = session.run(
                """
                MATCH (e:Entity {project_id: $project_id})
                WITH count(e) AS entities
                MATCH (p:Passage {project_id: $project_id})
                WITH entities, count(p) AS passages
                OPTIONAL MATCH (:Entity {project_id: $project_id})-[r:RELATES_TO]->()
                RETURN entities, passages, count(r) AS rels
                """,
                project_id=project_id,
            ).single()
            if not record:
                return GraphStats(0, 0, 0)
            return GraphStats(
                entity_count=record["entities"] or 0,
                passage_count=record["passages"] or 0,
                relationship_count=record["rels"] or 0,
            )

    def search_entities_by_vector(
        self,
        project_id: str,
        embedding: list[float],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        with self._get_driver().session() as session:
            try:
                result = session.run(
                    """
                    CALL db.index.vector.queryNodes('entity_embedding', $top_k, $embedding)
                    YIELD node, score
                    WHERE node.project_id = $project_id
                    RETURN node.entity_id AS entity_id,
                           node.name AS name,
                           node.description AS description,
                           score
                    ORDER BY score DESC
                    LIMIT $top_k
                    """,
                    project_id=project_id,
                    embedding=embedding,
                    top_k=top_k,
                )
                return [dict(r) for r in result]
            except Neo4jError:
                return self._search_entities_fulltext(project_id, "", top_k)

    def _search_entities_fulltext(
        self,
        project_id: str,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            with self._get_driver().session() as session:
                result = session.run(
                    """
                    MATCH (e:Entity {project_id: $project_id})
                    RETURN e.entity_id AS entity_id,
                           e.name AS name,
                           e.description AS description,
                           1.0 AS score
                    LIMIT $top_k
                    """,
                    project_id=project_id,
                    top_k=top_k,
                )
                return [dict(r) for r in result]
        with self._get_driver().session() as session:
            result = session.run(
                """
                CALL db.index.fulltext.queryNodes('entity_search', $query)
                YIELD node, score
                WHERE node.project_id = $project_id
                RETURN node.entity_id AS entity_id,
                       node.name AS name,
                       node.description AS description,
                       score
                ORDER BY score DESC
                LIMIT $top_k
                """,
                project_id=project_id,
                query=query,
                top_k=top_k,
            )
            return [dict(r) for r in result]

    def search_entities_for_query(
        self,
        project_id: str,
        query: str,
        embedding: list[float] | None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        if embedding:
            vector_hits = self.search_entities_by_vector(
                project_id, embedding, top_k=top_k
            )
            if vector_hits:
                return vector_hits
        return self._search_entities_fulltext(project_id, query, top_k)

    def get_passages_for_entities(
        self,
        project_id: str,
        entity_ids: list[str],
        max_hops: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not entity_ids:
            return []
        hops = max(1, min(max_hops, 5))
        with self._get_driver().session() as session:
            result = session.run(
                f"""
                MATCH (seed:Entity {{project_id: $project_id}})
                WHERE seed.entity_id IN $entity_ids
                OPTIONAL MATCH (seed)-[:RELATES_TO*0..{hops}]-(related:Entity {{project_id: $project_id}})
                WITH collect(DISTINCT seed) + collect(DISTINCT related) AS entities
                UNWIND entities AS ent
                MATCH (p:Passage {{project_id: $project_id}})-[:MENTIONS]->(ent)
                MATCH (p)-[:FROM_DOCUMENT]->(d:Document)
                RETURN DISTINCT p.passage_id AS passage_id,
                       p.content AS content,
                       p.chunk_index AS chunk_index,
                       p.document_id AS document_id,
                       d.filename AS filename,
                       ent.name AS entity_name,
                       1.0 AS score
                LIMIT $limit
                """,
                project_id=project_id,
                entity_ids=entity_ids,
                limit=limit,
            )
            return [dict(r) for r in result]

    def search_passages_fulltext(
        self,
        project_id: str,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self._get_driver().session() as session:
            try:
                result = session.run(
                    """
                    CALL db.index.fulltext.queryNodes('passage_content', $query)
                    YIELD node, score
                    WHERE node.project_id = $project_id
                    MATCH (node)-[:FROM_DOCUMENT]->(d:Document)
                    RETURN node.passage_id AS passage_id,
                           node.content AS content,
                           node.chunk_index AS chunk_index,
                           node.document_id AS document_id,
                           d.filename AS filename,
                           score
                    ORDER BY score DESC
                    LIMIT $limit
                    """,
                    project_id=project_id,
                    query=query,
                    limit=limit,
                )
                return [dict(r) for r in result]
            except Neo4jError:
                result = session.run(
                    """
                    MATCH (p:Passage {project_id: $project_id})
                    WHERE toLower(p.content) CONTAINS toLower($query)
                    MATCH (p)-[:FROM_DOCUMENT]->(d:Document)
                    RETURN p.passage_id AS passage_id,
                           p.content AS content,
                           p.chunk_index AS chunk_index,
                           p.document_id AS document_id,
                           d.filename AS filename,
                           1.0 AS score
                    LIMIT $limit
                    """,
                    project_id=project_id,
                    query=query,
                    limit=limit,
                )
                return [dict(r) for r in result]


_neo4j_store: Neo4jStore | None = None


def get_neo4j_store() -> Neo4jStore:
    global _neo4j_store
    if _neo4j_store is None:
        _neo4j_store = Neo4jStore()
    return _neo4j_store
