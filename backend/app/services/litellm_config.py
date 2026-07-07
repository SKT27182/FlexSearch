"""Single source of truth for LiteLLM endpoint resolution + global config."""

from __future__ import annotations

from dataclasses import dataclass

import litellm

from app.core.config import settings
from app.services.model_ids import extract_litellm_provider, is_local_embedding_model

_configured = False


def configure_litellm() -> None:
    """Idempotent global LiteLLM config (verbosity)."""
    global _configured
    if _configured:
        return
    litellm.set_verbose = settings.debug
    _configured = True


@dataclass(frozen=True)
class LiteLLMEndpoint:
    model: str
    api_key: str
    api_base: str | None
    provider: str
    is_local: bool


def _mk(model: str, api_key: str, api_base: str) -> LiteLLMEndpoint:
    return LiteLLMEndpoint(
        model=model,
        api_key=api_key or "",
        api_base=api_base or None,
        provider=extract_litellm_provider(model),
        is_local=is_local_embedding_model(model),
    )


def llm_endpoint() -> LiteLLMEndpoint:
    return _mk(settings.model_name, settings.api_key, settings.llm_api_base)


def vector_embedding_endpoint() -> LiteLLMEndpoint:
    key = settings.embedding_api_key or settings.api_key
    return _mk(settings.embedding_model, key, settings.embedding_api_base)


def graphrag_embedding_endpoint() -> LiteLLMEndpoint:
    key = (
        settings.graphrag_embedding_api_key
        or settings.embedding_api_key
        or settings.api_key
    )
    base = settings.graphrag_embedding_api_base or settings.embedding_api_base
    return _mk(settings.graphrag_embedding_model, key, base)
