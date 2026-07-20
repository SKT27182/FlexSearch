"""Single source of truth for LiteLLM endpoint resolution + global config."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.core.config import settings
from app.services.model_ids import extract_litellm_provider, is_local_embedding_model

# LiteLLM eagerly pre-loads Bedrock/SageMaker event-stream shapes via botocore
# at import time. FlexSearch does not use those providers; suppress the noisy
# optional-dependency warnings without pulling in AWS SDKs.
_AWS_PRELOAD_MARKERS = (
    "could not pre-load bedrock-runtime",
    "could not pre-load sagemaker-runtime",
)


def _configure_litellm_log_level() -> None:
    """Set LiteLLM's supported log control without overriding user config."""
    os.environ.setdefault("LITELLM_LOG", "DEBUG" if settings.debug else "INFO")


class _SuppressOptionalAwsPreload(logging.Filter):
    """Drop LiteLLM warnings about missing botocore for unused AWS providers."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(marker in msg for marker in _AWS_PRELOAD_MARKERS)


def _install_optional_aws_warning_filter() -> None:
    logger = logging.getLogger("LiteLLM")
    if any(isinstance(f, _SuppressOptionalAwsPreload) for f in logger.filters):
        return
    logger.addFilter(_SuppressOptionalAwsPreload())


_install_optional_aws_warning_filter()
_configure_litellm_log_level()

import litellm  # noqa: E402, F401  — configure/filter before this import

_configured = False


def configure_litellm() -> None:
    """Idempotent global LiteLLM config (verbosity + noise filters)."""
    global _configured
    _install_optional_aws_warning_filter()
    _configure_litellm_log_level()
    if _configured:
        return
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
