"""GraphRAG workspace bootstrap tests."""

from __future__ import annotations

from pathlib import Path
from string import Template

import pandas as pd
import pytest

from app.services.graphrag_workspace import (
    GraphRAGWorkspace,
    _CompactingJsonCache,
    build_graphml_bytes,
    _destructive_graph_changes,
    _prepare_cache_compaction_workspace,
    _prepare_full_rebuild_workspace,
    _prepare_index_input_documents,
    _configured_graphrag_vector_size,
    _embedding_section_uses_legacy_api_key,
    _has_incremental_update_baseline,
    _needs_config_refresh,
    _patch_graphrag_vector_size,
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


def test_patch_graphrag_vector_size_inserts_and_replaces() -> None:
    initial = (
        "vector_store:\n  type: lancedb\n  db_uri: output/lancedb\n\nembed_text:\n"
    )
    patched = _patch_graphrag_vector_size(initial, 768)
    assert _configured_graphrag_vector_size(patched) == 768

    replaced = _patch_graphrag_vector_size(patched, 1024)
    assert _configured_graphrag_vector_size(replaced) == 1024
    assert replaced.count("vector_size:") == 1


def test_incremental_update_requires_complete_previous_output(tmp_path: Path) -> None:
    from app.services.graphrag_workspace import PARQUET_FILES

    output = tmp_path / "output"
    output.mkdir()
    assert _has_incremental_update_baseline(tmp_path) is False

    for name in PARQUET_FILES:
        (output / name).touch()
    assert _has_incremental_update_baseline(tmp_path) is True

    (output / "documents.parquet").unlink()
    assert _has_incremental_update_baseline(tmp_path) is False


def test_needs_config_refresh_detects_embedding_dimension_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "old-vector-size"
    root.mkdir()
    (root / "settings.yaml").write_text(
        "concurrent_requests: 4\nvector_store:\n  type: lancedb\n  vector_size: 3072\n",
        encoding="utf-8",
    )
    (root / "prompts").mkdir()
    (root / "prompts" / "extract_graph.txt").write_text("prompt", encoding="utf-8")
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_embedding_dimension",
        768,
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_embedding_api_base", ""
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.embedding_api_base", ""
    )
    monkeypatch.setattr("app.services.graphrag_workspace.settings.llm_api_base", "")

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
        "app.services.graphrag_workspace.settings.llm_api_base",
        "",
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_embedding_model",
        "openai/text-embedding-3-small",
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_embedding_dimension",
        768,
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.embedding_api_base",
        "http://embed-proxy:4000",
    )
    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_embedding_api_base",
        "",
    )
    # load_config expands ${GRAPHRAG_*} from the process env (not Settings).
    monkeypatch.setenv("GRAPHRAG_API_KEY", "test-llm-api-key")
    monkeypatch.setenv("GRAPHRAG_EMBEDDING_API_KEY", "test-embedding-api-key")
    monkeypatch.setenv("GRAPHRAG_EMBEDDING_API_BASE", "http://embed-proxy:4000")

    root = tmp_path / "workspace"
    GraphRAGWorkspace(storage=object()).bootstrap_workspace(root, force=True)
    yaml = (root / "settings.yaml").read_text(encoding="utf-8")
    config = load_config(root)
    completion = config.get_completion_model_config("default_completion_model")
    embedding = config.get_embedding_model_config("default_embedding_model")

    assert completion.model_provider == "gemini"
    assert completion.model == "gemini-3.1-flash-lite"
    assert embedding.model_provider == "openai"
    assert embedding.model == "text-embedding-3-small"
    assert config.vector_store.vector_size == 768
    assert all(
        schema.vector_size == 768
        for schema in config.vector_store.index_schema.values()
    )
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
        GraphRAGWorkspace(storage=object()).bootstrap_workspace(
            tmp_path / "workspace", force=True
        )


def test_bootstrap_workspace_writes_graphrag3_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("graphrag")
    from graphrag.config.load_config import load_config

    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.llm_api_base",
        "",
    )
    monkeypatch.setenv("GRAPHRAG_API_KEY", "test-key")
    monkeypatch.setenv("GRAPHRAG_EMBEDDING_API_KEY", "embed-key")
    monkeypatch.setenv(
        "GRAPHRAG_EMBEDDING_API_BASE", "http://embed-proxy:4000/v1"
    )

    root = tmp_path / "workspace"
    GraphRAGWorkspace(storage=object()).bootstrap_workspace(root, force=True)
    yaml = (root / "settings.yaml").read_text(encoding="utf-8")
    Template(yaml).substitute(
        GRAPHRAG_API_KEY="test-key",
        GRAPHRAG_EMBEDDING_API_KEY="embed-key",
        GRAPHRAG_EMBEDDING_API_BASE="http://embed-proxy:4000/v1",
    )
    assert "type: json" in yaml
    assert (root / "prompts" / "extract_graph.txt").exists()
    load_config(root)


def test_bootstrap_workspace_keeps_materialized_workspace_without_force(
    tmp_path: Path,
) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    settings_path = root / "settings.yaml"
    original = "existing: configuration\n"
    settings_path.write_text(original, encoding="utf-8")

    GraphRAGWorkspace(storage=object()).bootstrap_workspace(root, force=False)

    assert settings_path.read_text(encoding="utf-8") == original


def test_prepare_update_input_repairs_baseline_and_excludes_existing_document(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    pd.DataFrame(
        [{"id": "doc-1", "title": "Existing", "human_readable_id": None}]
    ).to_parquet(output / "documents.parquet", index=False)
    documents = [
        {"id": "doc-1", "title": "Existing", "text": "old"},
        {"id": "doc-2", "title": "New", "text": "new"},
    ]

    delta = _prepare_index_input_documents(
        tmp_path,
        documents,
        is_update=True,
    )

    assert delta["id"].tolist() == ["doc-2"]
    assert pd.read_parquet(output / "documents.parquet")[
        "human_readable_id"
    ].tolist() == [0]


def test_destructive_changes_distinguish_add_only_from_mixed_changes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    pd.DataFrame(
        [
            {"id": "keep", "text": "unchanged"},
            {"id": "delete", "text": "old"},
        ]
    ).to_parquet(output / "documents.parquet", index=False)

    added_only = [
        {"id": "keep", "text": "unchanged"},
        {"id": "delete", "text": "old"},
        {"id": "new", "text": "new"},
    ]
    assert _destructive_graph_changes(tmp_path, added_only) == (set(), set())

    mixed = [
        {"id": "keep", "text": "unchanged"},
        {"id": "new", "text": "new"},
    ]
    assert _destructive_graph_changes(tmp_path, mixed) == ({"delete"}, set())


def test_full_rebuild_preserves_cache_but_clears_derived_artifacts(
    tmp_path: Path,
) -> None:
    for subdir in ("input", "output", "logs", "cache"):
        path = tmp_path / subdir
        path.mkdir()
        (path / "old").write_text(subdir, encoding="utf-8")

    _prepare_full_rebuild_workspace(tmp_path)

    assert (tmp_path / "cache" / "old").read_text(encoding="utf-8") == "cache"
    for subdir in ("input", "output", "logs"):
        assert list((tmp_path / subdir).iterdir()) == []


@pytest.mark.asyncio
async def test_compacting_cache_keeps_only_entries_used_by_rebuild(
    tmp_path: Path,
) -> None:
    from graphrag_cache.json_cache import JsonCache
    from graphrag_storage.file_storage import FileStorage

    old_cache = tmp_path / "cache"
    source_storage = FileStorage(base_dir=str(old_cache))
    source_cache = JsonCache(storage=source_storage)
    await source_cache.set("used", {"value": 1})
    await source_cache.set("deleted-document-only", {"value": 2})

    _prepare_cache_compaction_workspace(tmp_path)
    active_storage = FileStorage(base_dir=str(tmp_path / "cache"))
    compacting = _CompactingJsonCache(
        storage=active_storage,
        source_storage={
            "type": "file",
            "base_dir": str(tmp_path / "cache_source"),
        },
    )

    assert await compacting.get("used") == {"value": 1}
    await compacting.set("new-document", {"value": 3})

    active_cache = JsonCache(storage=active_storage)
    assert await active_cache.has("used")
    assert await active_cache.has("new-document")
    assert not await active_cache.has("deleted-document-only")


def test_build_graphml_bytes_uses_merged_entities_and_relationships() -> None:
    import networkx as nx

    entities = pd.DataFrame(
        [
            {
                "id": "entity-1",
                "human_readable_id": 0,
                "title": "PROJECT ORION",
                "type": "ORGANIZATION",
                "frequency": 1,
                "degree": 1,
                "description": "New project",
                "text_unit_ids": ["unit-2"],
            },
            {
                "id": "entity-2",
                "human_readable_id": 1,
                "title": "NEO4J",
                "type": "ORGANIZATION",
                "frequency": 1,
                "degree": 1,
                "description": "Graph database",
                "text_unit_ids": ["unit-2"],
            },
        ]
    )
    relationships = pd.DataFrame(
        [
            {
                "id": "relationship-1",
                "human_readable_id": 0,
                "source": "PROJECT ORION",
                "target": "NEO4J",
                "weight": 8.0,
                "combined_degree": 2,
                "description": "Orion uses Neo4j",
                "text_unit_ids": ["unit-2"],
            }
        ]
    )

    graphml = build_graphml_bytes(entities, relationships)
    graph = nx.parse_graphml(graphml.decode("utf-8"))

    assert set(graph.nodes) == {"PROJECT ORION", "NEO4J"}
    assert graph.number_of_edges() == 1
    assert graph.nodes["PROJECT ORION"]["entity_id"] == "entity-1"
    assert graph.edges["PROJECT ORION", "NEO4J"]["weight"] == 8.0
