"""Tests for LiteLLM endpoint resolution."""

from __future__ import annotations

import logging
import os

import pytest

from app.core.config import Settings
from app.services.litellm_config import (
    LiteLLMEndpoint,
    _SuppressOptionalAwsPreload,
    configure_litellm,
    graphrag_embedding_endpoint,
    llm_endpoint,
    vector_embedding_endpoint,
)
import litellm  # after litellm_config installs AWS-preload filter


def test_vector_embedding_endpoint_local_when_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.litellm_config.settings",
        Settings(
            postgres_user="u",
            postgres_password="p",
            minio_access_key="a",
            minio_secret_key="s",
            jwt_secret="secret",
            embedding_model="openai/text-embedding-3-small",
            embedding_api_key="",
            embedding_api_base="",
            api_key="",
        ),
    )
    ep = vector_embedding_endpoint()
    assert ep.is_local is True
    assert ep.model == "sentence-transformers/all-MiniLM-L6-v2"
    assert ep.api_key == ""


def test_vector_embedding_endpoint_api_with_key_and_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.litellm_config.settings",
        Settings(
            postgres_user="u",
            postgres_password="p",
            minio_access_key="a",
            minio_secret_key="s",
            jwt_secret="secret",
            embedding_model="openai/text-embedding-3-small",
            embedding_api_key="embed-key",
            embedding_api_base="http://localhost:4000",
            api_key="llm-key",
        ),
    )
    ep = vector_embedding_endpoint()
    assert ep.is_local is False
    assert ep.api_key == "embed-key"
    assert ep.api_base == "http://localhost:4000"


def test_graphrag_embedding_endpoint_key_cascade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.litellm_config.settings",
        Settings(
            postgres_user="u",
            postgres_password="p",
            minio_access_key="a",
            minio_secret_key="s",
            jwt_secret="secret",
            graphrag_embedding_model="gemini/text-embedding-004",
            graphrag_embedding_api_key="",
            embedding_api_key="embed-key",
            api_key="llm-key",
        ),
    )
    ep = graphrag_embedding_endpoint()
    assert ep.api_key == "embed-key"
    assert ep.is_local is False


def test_graphrag_embedding_endpoint_base_cascade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.litellm_config.settings",
        Settings(
            postgres_user="u",
            postgres_password="p",
            minio_access_key="a",
            minio_secret_key="s",
            jwt_secret="secret",
            graphrag_embedding_model="openai/text-embedding-3-small",
            graphrag_embedding_api_base="",
            embedding_api_base="http://proxy:4000",
            api_key="key",
        ),
    )
    ep = graphrag_embedding_endpoint()
    assert ep.api_base == "http://proxy:4000"


def test_graphrag_embedding_endpoint_never_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.litellm_config.settings",
        Settings(
            postgres_user="u",
            postgres_password="p",
            minio_access_key="a",
            minio_secret_key="s",
            jwt_secret="secret",
            embedding_model="sentence-transformers/all-MiniLM-L6-v2",
            embedding_api_key="",
            graphrag_embedding_model="openai/text-embedding-3-small",
            api_key="",
        ),
    )
    ep = graphrag_embedding_endpoint()
    assert ep.is_local is False


def test_llm_endpoint_includes_api_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.litellm_config.settings",
        Settings(
            postgres_user="u",
            postgres_password="p",
            minio_access_key="a",
            minio_secret_key="s",
            jwt_secret="secret",
            model_name="gpt-4o-mini",
            api_key="sk-test",
            llm_api_base="http://localhost:4000",
        ),
    )
    ep = llm_endpoint()
    assert ep.api_base == "http://localhost:4000"
    assert ep.api_key == "sk-test"


def test_configure_litellm_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.litellm_config._configured", False)
    monkeypatch.delenv("LITELLM_LOG", raising=False)
    monkeypatch.setattr(
        "app.services.litellm_config.settings",
        Settings(
            postgres_user="u",
            postgres_password="p",
            minio_access_key="a",
            minio_secret_key="s",
            jwt_secret="secret",
            debug=True,
        ),
    )
    configure_litellm()
    assert os.environ["LITELLM_LOG"] == "DEBUG"
    assert litellm.set_verbose is False
    configure_litellm()
    assert os.environ["LITELLM_LOG"] == "DEBUG"
    assert litellm.set_verbose is False


def test_configure_litellm_preserves_explicit_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.services.litellm_config._configured", False)
    monkeypatch.setenv("LITELLM_LOG", "ERROR")
    configure_litellm()
    assert os.environ["LITELLM_LOG"] == "ERROR"


def test_optional_aws_preload_filter_installed() -> None:
    logger = logging.getLogger("LiteLLM")
    assert any(isinstance(f, _SuppressOptionalAwsPreload) for f in logger.filters)


def test_optional_aws_preload_filter_drops_botocore_noise() -> None:
    filt = _SuppressOptionalAwsPreload()
    drop = logging.LogRecord(
        name="LiteLLM",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg=(
            "litellm: could not pre-load bedrock-runtime response stream shape "
            "— Bedrock event-stream decoding will be unavailable. "
            "Error: No module named 'botocore'"
        ),
        args=(),
        exc_info=None,
    )
    keep = logging.LogRecord(
        name="LiteLLM",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="rate limit exceeded for model gpt-4o-mini",
        args=(),
        exc_info=None,
    )
    assert filt.filter(drop) is False
    assert filt.filter(keep) is True


def test_litellm_endpoint_dataclass() -> None:
    ep = LiteLLMEndpoint(
        model="openai/text-embedding-3-small",
        api_key="k",
        api_base="http://x",
        provider="openai",
        is_local=False,
    )
    assert ep.model == "openai/text-embedding-3-small"
