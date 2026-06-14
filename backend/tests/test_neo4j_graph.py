"""Tests for graph RAG extraction and config."""

import json
from unittest.mock import AsyncMock, patch

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
