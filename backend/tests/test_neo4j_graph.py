"""Tests for graph RAG extraction and config."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import RagMode
from app.rag.graph.extractor import GraphExtractor, _parse_json
from app.schemas.rag_config import (
    GRAPH_RETRIEVAL_STRATEGIES,
    VECTOR_RETRIEVAL_STRATEGIES,
    GraphRagConfig,
    VectorRagConfig,
    parse_rag_config,
)


def test_parse_json_from_fence() -> None:
    raw = '```json\n{"entities": [], "relationships": []}\n```'
    data = _parse_json(raw)
    assert data == {"entities": [], "relationships": []}


def test_parse_rag_config_vector_default() -> None:
    cfg = parse_rag_config(RagMode.VECTOR, None)
    assert isinstance(cfg, VectorRagConfig)


def test_parse_rag_config_graph_default() -> None:
    cfg = parse_rag_config(RagMode.GRAPH, None)
    assert isinstance(cfg, GraphRagConfig)


def test_graph_entity_id_stable() -> None:
    a = GraphExtractor.entity_id("proj-1", "Alice")
    b = GraphExtractor.entity_id("proj-1", "Alice")
    c = GraphExtractor.entity_id("proj-1", "Bob")
    assert a == b
    assert a != c


@pytest.mark.asyncio
async def test_extractor_parses_llm_response() -> None:
    payload = {
        "entities": [
            {"name": "Alice", "type": "Person", "description": "Engineer"},
            {"name": "Acme", "type": "Org", "description": "Company"},
        ],
        "relationships": [
            {
                "source": "Alice",
                "target": "Acme",
                "type": "WORKS_AT",
                "description": "employment",
            }
        ],
    }
    mock_response = AsyncMock()
    mock_response.content = json.dumps(payload)

    with patch("app.rag.graph.extractor.get_llm_service") as mock_llm:
        mock_llm.return_value.complete = AsyncMock(return_value=mock_response)
        extractor = GraphExtractor(max_entities=10)
        result = await extractor.extract("proj-1", "Alice works at Acme.")

    assert len(result.entities) == 2
    assert len(result.relationships) == 1
    assert result.entities[0].name == "Alice"


def test_ensure_entity_vector_index_creates_when_missing() -> None:
    from app.services.neo4j_store import Neo4jStore

    store = Neo4jStore()
    session = MagicMock()
    session.run.return_value.single.return_value = None  # no existing index

    store._ensure_entity_vector_index(session, 384)

    # SHOW INDEXES + CREATE VECTOR INDEX
    assert session.run.call_count >= 2
    create_calls = [
        c.args[0] for c in session.run.call_args_list if c.args and "CREATE VECTOR INDEX" in c.args[0]
    ]
    assert create_calls
    assert "384" in create_calls[0]


def test_ensure_entity_vector_index_recreates_on_dimension_mismatch() -> None:
    from app.services.neo4j_store import Neo4jStore

    store = Neo4jStore()
    session = MagicMock()
    session.run.return_value.single.return_value = {
        "options": {"indexConfig": {"vector.dimensions": 384}}
    }

    store._ensure_entity_vector_index(session, 768)

    stmts = [c.args[0] for c in session.run.call_args_list if c.args]
    assert any("DROP INDEX entity_embedding" in s for s in stmts)
    assert any("SET e.embedding = null" in s for s in stmts)
    assert any("CREATE VECTOR INDEX entity_embedding" in s and "768" in s for s in stmts)


def test_ensure_entity_vector_index_noop_when_dim_matches() -> None:
    from app.services.neo4j_store import Neo4jStore

    store = Neo4jStore()
    session = MagicMock()
    session.run.return_value.single.return_value = {
        "options": {"indexConfig": {"vector.dimensions": 384}}
    }

    store._ensure_entity_vector_index(session, 384)

    # Only SHOW INDEXES — no drop/create
    assert session.run.call_count == 1
    assert "SHOW INDEXES" in session.run.call_args.args[0]

