"""Tests for unified embedding service routing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.embedding import EmbeddingService, get_graphrag_embedding_service
from app.services.litellm_config import graphrag_embedding_endpoint
from app.services.model_ids import is_local_embedding_model


def test_is_local_embedding_model_detects_sentence_transformers() -> None:
    assert is_local_embedding_model("sentence-transformers/all-MiniLM-L6-v2")
    assert not is_local_embedding_model("openai/text-embedding-3-small")


def test_embedding_service_uses_local_backend_for_sentence_transformers() -> None:
    service = EmbeddingService(model_id="sentence-transformers/all-MiniLM-L6-v2")
    assert service.uses_local_model
    assert service.provider == "local"


def test_embedding_service_forwards_api_base_to_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import embedding as embedding_module

    mock_response = MagicMock()
    mock_response.data = [{"embedding": [0.1, 0.2, 0.3]}]
    mock_embed = MagicMock(return_value=mock_response)
    monkeypatch.setattr(embedding_module, "embedding", mock_embed)

    service = embedding_module.EmbeddingService(
        model_id="openai/text-embedding-3-small",
        api_key="test-key",
        api_base="http://localhost:4000",
    )
    service.embed_batch(["hello"])

    mock_embed.assert_called_once_with(
        model="openai/text-embedding-3-small",
        input=["hello"],
        api_key="test-key",
        api_base="http://localhost:4000",
    )


def test_graphrag_embedding_service_uses_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.services.embedding as embedding_module

    embedding_module._graphrag_embedding_service = None
    monkeypatch.setattr(
        "app.services.litellm_config.settings.graphrag_embedding_api_key",
        "",
    )
    monkeypatch.setattr(
        "app.services.litellm_config.settings.embedding_api_key",
        "embed-cascade-key",
    )
    monkeypatch.setattr(
        "app.services.litellm_config.settings.embedding_api_base",
        "http://embed-proxy:4000",
    )
    monkeypatch.setattr(
        "app.services.litellm_config.settings.graphrag_embedding_api_base",
        "",
    )

    ep = graphrag_embedding_endpoint()
    service = get_graphrag_embedding_service()

    assert service._api_key == ep.api_key
    assert service._api_base == ep.api_base
    assert service._api_key == "embed-cascade-key"
    assert service._api_base == "http://embed-proxy:4000"

    embedding_module._graphrag_embedding_service = None
