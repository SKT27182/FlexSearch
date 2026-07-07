"""Shared LiteLLM / local model id helpers."""

from __future__ import annotations

LOCAL_EMBEDDING_PREFIXES = (
    "sentence-transformers/",
    "huggingface/",
    "local/",
)


def is_local_embedding_model(model_id: str) -> bool:
    """True when embeddings run locally (sentence-transformers), not via API."""
    return any(model_id.startswith(prefix) for prefix in LOCAL_EMBEDDING_PREFIXES)


def split_litellm_model(model_id: str) -> tuple[str, str]:
    """Split a LiteLLM model id into (provider, model) for GraphRAG settings."""
    if "/" in model_id:
        provider, model = model_id.split("/", 1)
        return provider, model
    if model_id.startswith("gpt"):
        return "openai", model_id
    if model_id.startswith("claude"):
        return "anthropic", model_id
    if model_id.startswith("gemini"):
        return "gemini", model_id
    return "openai", model_id


def extract_litellm_provider(model_id: str) -> str:
    """Provider slug from a LiteLLM model id."""
    if is_local_embedding_model(model_id):
        return "local"
    if "/" in model_id:
        return model_id.split("/", 1)[0]
    if model_id.startswith("gpt"):
        return "openai"
    if model_id.startswith("claude"):
        return "anthropic"
    if model_id.startswith("gemini"):
        return "gemini"
    return "unknown"
