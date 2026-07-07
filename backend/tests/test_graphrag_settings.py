"""GraphRAG workspace bootstrap tests."""

from __future__ import annotations

from pathlib import Path
from string import Template

import pytest

from app.services.graphrag_workspace import (
    GraphRAGWorkspace,
    _embedding_section_uses_legacy_api_key,
    _needs_config_refresh,
)
from app.services.model_ids import split_litellm_model


def test_needs_config_refresh_detects_legacy_cache_type() -> None:
    root = Path("/tmp/legacy-graphrag")
    root.mkdir(parents=True, exist_ok=True)
    (root / "settings.yaml").write_text(
        "cache:\n  type: file\n  base_dir: cache\n",
        encoding="utf-8",
    )
    assert _needs_config_refresh(root)


def test_embedding_section_uses_legacy_api_key() -> None:
    text = (
        "embedding_models:\n"
        "  default_embedding_model:\n"
        "    api_key: ${GRAPHRAG_API_KEY}\n"
    )
    assert _embedding_section_uses_legacy_api_key(text) is True


def test_needs_config_refresh_detects_legacy_embedding_api_key(tmp_path: Path) -> None:
    root = tmp_path / "legacy-embedding-key"
    root.mkdir()
    (root / "settings.yaml").write_text(
        "embedding_models:\n"
        "  default_embedding_model:\n"
        "    api_key: ${GRAPHRAG_API_KEY}\n",
        encoding="utf-8",
    )
    (root / "prompts").mkdir()
    (root / "prompts" / "extract_graph.txt").write_text("prompt", encoding="utf-8")
    assert _needs_config_refresh(root)


def test_split_litellm_model_parses_provider_prefix() -> None:
    assert split_litellm_model("gemini/gemini-3.1-flash-lite") == (
        "gemini",
        "gemini-3.1-flash-lite",
    )


def test_bootstrap_workspace_splits_llm_and_embedding_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("graphrag")
    from graphrag.config.load_config import load_config

    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.model_name",
        "gemini/gemini-3.1-flash-lite",
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_embedding_model",
        "openai/text-embedding-3-small",
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.embedding_api_base",
        "http://embed-proxy:4000",
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_embedding_api_base",
        "",
    )

    root = tmp_path / "workspace"
    GraphRAGWorkspace().bootstrap_workspace(root, force=True)
    yaml = (root / "settings.yaml").read_text(encoding="utf-8")
    config = load_config(root)
    completion = config.get_completion_model_config("default_completion_model")
    embedding = config.get_embedding_model_config("default_embedding_model")

    assert completion.model_provider == "gemini"
    assert completion.model == "gemini-3.1-flash-lite"
    assert embedding.model_provider == "openai"
    assert embedding.model == "text-embedding-3-small"
    assert "api_key: ${GRAPHRAG_EMBEDDING_API_KEY}" in yaml
    assert "api_base: ${GRAPHRAG_EMBEDDING_API_BASE}" in yaml
    assert _embedding_section_uses_legacy_api_key(yaml) is False


def test_bootstrap_workspace_rejects_local_embedding_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("graphrag")
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_embedding_model",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    with pytest.raises(ValueError, match="GRAPHRAG_EMBEDDING_MODEL"):
        GraphRAGWorkspace().bootstrap_workspace(tmp_path / "workspace", force=True)


def test_bootstrap_workspace_writes_graphrag3_settings(tmp_path: Path) -> None:
    pytest.importorskip("graphrag")
    from graphrag.config.load_config import load_config

    root = tmp_path / "workspace"
    GraphRAGWorkspace().bootstrap_workspace(root, force=True)
    yaml = (root / "settings.yaml").read_text(encoding="utf-8")
    Template(yaml).substitute(
        GRAPHRAG_API_KEY="test-key",
        GRAPHRAG_EMBEDDING_API_KEY="embed-key",
    )
    assert "type: json" in yaml
    assert (root / "prompts" / "extract_graph.txt").exists()
    load_config(root)
