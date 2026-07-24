"""Microsoft GraphRAG workspace: MinIO sync, indexing, and search materialization."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Document, DocumentStatus, Project, RagMode
from app.db.postgres import async_session_maker
from app.schemas.graph_index import GraphIndexState
from app.schemas.rag_config import GraphRagConfig as AppGraphRagConfig
from app.services.document_storage import extracted_md_key
from app.services.graphrag_runner import run_in_std_event_loop, run_sync_in_std_thread
from app.services.graphrag_failfast import install_graphrag_failfast
from app.services.graphrag_rate_limit import install_graphrag_rate_limit_retry
from app.services.litellm_config import graphrag_embedding_endpoint
from app.services.model_ids import is_local_embedding_model, split_litellm_model
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__)


class _LoggingWorkflowCallbacks:
    """Forward GraphRAG pipeline/workflow events to our logger.

    Implements the GraphRAG ``WorkflowCallbacks`` protocol so a long Microsoft
    GraphRAG build is visible in ``~/.local/share/projects/flexsearch/dev-logs/backend.log``
    instead of silent. The callbacks run inside the stdlib-loop worker thread,
    so we rely on the thread-safe stdlib logging behind ``create_logger`` and
    keep messages concise. Signatures must match the protocol exactly — e.g.
    ``workflow_start`` / ``workflow_end`` take ``(name, instance)``.
    """

    def pipeline_start(self, names: list[str]) -> None:
        logger.info("GraphRAG pipeline starting; workflows=%s", names)

    def pipeline_end(self, results: list) -> None:
        logger.info("GraphRAG pipeline ended; workflows=%d", len(results or []))

    def workflow_start(self, name: str, instance: object) -> None:
        logger.info("GraphRAG workflow start: %s", name)

    def workflow_end(self, name: str, instance: object) -> None:
        logger.info("GraphRAG workflow done: %s", name)

    def progress(self, progress) -> None:
        # GraphRAG passes a Progress dataclass (completed_items/total_items/description).
        try:
            done = getattr(progress, "completed_items", None)
            total = getattr(progress, "total_items", None)
            desc = getattr(progress, "description", None)
            logger.info(
                "GraphRAG progress: %s [%s/%s]",
                desc or name_of(progress),
                done,
                total,
            )
        except Exception:  # noqa: BLE001 - never let logging crash the build
            logger.info("GraphRAG progress: %s", progress)

    def pipeline_error(self, error: BaseException) -> None:
        logger.error("GraphRAG pipeline error: %s", error)


def name_of(obj: object) -> str:
    return type(obj).__name__


def _build_workflow_callbacks() -> list:
    """Return the callback list to pass to GraphRAG build_index."""
    return [_LoggingWorkflowCallbacks()]


def _indexing_method_for(method_name: str):
    """Map FlexSearch config method to a GraphRAG IndexingMethod enum member.

    GraphRAG's enum only exposes Standard / Fast (plus their *-update variants
    derived internally from ``is_update_run``). Older code referenced an
    ``IndexingMethod.NLP`` member that never existed in the installed version,
    which raised ``AttributeError: NLP`` and aborted the build instantly. The
    "nlp" config option is GraphRAG's Fast (NLP noun-phrase) graph build.
    """
    from graphrag.config.enums import IndexingMethod

    normalized = (method_name or "").strip().lower()
    if normalized in {"nlp", "fast"}:
        return IndexingMethod.Fast
    if normalized in {"standard", "std"}:
        return IndexingMethod.Standard
    logger.warning(
        "Unknown GraphRAG indexing method %r; falling back to Standard",
        method_name,
    )
    return IndexingMethod.Standard


PARQUET_FILES = (
    "documents.parquet",
    "entities.parquet",
    "relationships.parquet",
    "communities.parquet",
    "community_reports.parquet",
    "text_units.parquet",
)
GRAPHML_CANDIDATES = (
    "graph.graphml",
    "graph_snapshot.graphml",
    "artifacts/graph.graphml",
    "artifacts/snapshots/graph.graphml",
)
FULL_REBUILD_REPLACED_SUBDIRS = ("input", "output", "logs")
COMPACTING_CACHE_TYPE = "flexsearch_compacting_json"


def graphrag_storage_prefix(project_id: UUID | str, generation: int) -> str:
    return f"projects/{project_id}/graphrag/generations/{generation}"


def _has_incremental_update_baseline(root: Path) -> bool:
    """True when a prior successful GraphRAG output can support an update run."""
    output = root / "output"
    return all((output / name).is_file() for name in PARQUET_FILES)


def _baseline_document_text(root: Path) -> dict[str, str]:
    """Return baseline document text keyed by stable document id."""
    path = root / "output" / "documents.parquet"
    if not path.is_file():
        return {}
    frame = pd.read_parquet(path, columns=["id", "text"])
    return dict(zip(frame["id"].astype(str), frame["text"].fillna("").astype(str)))


def _destructive_graph_changes(
    root: Path, documents: list[dict[str, Any]]
) -> tuple[set[str], set[str]]:
    """Return baseline ids that were deleted or changed in active sources."""
    baseline_text = _baseline_document_text(root)
    active_text = {str(document["id"]): str(document["text"]) for document in documents}
    baseline_ids = set(baseline_text)
    active_ids = set(active_text)
    deleted_ids = baseline_ids - active_ids
    changed_ids = {
        document_id
        for document_id in baseline_ids & active_ids
        if baseline_text[document_id] != active_text[document_id]
    }
    return deleted_ids, changed_ids


def _prepare_full_rebuild_workspace(root: Path) -> None:
    """Remove derived data while preserving the content-addressed LLM cache."""
    for subdir in FULL_REBUILD_REPLACED_SUBDIRS:
        path = root / subdir
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)


def _prepare_cache_compaction_workspace(root: Path) -> None:
    """Move the old cache aside and create an empty cache for live entries."""
    cache = root / "cache"
    source = root / "cache_source"
    shutil.rmtree(source, ignore_errors=True)
    if cache.exists():
        cache.rename(source)
    else:
        source.mkdir(parents=True)
    cache.mkdir(parents=True, exist_ok=True)


class _CompactingJsonCache:
    """Read-through cache that retains only entries used by the current build."""

    def __init__(self, storage=None, source_storage=None, **_kwargs: Any) -> None:
        from graphrag_cache.json_cache import JsonCache
        from graphrag_storage import StorageConfig, create_storage

        if source_storage is None:
            raise ValueError("Compacting cache requires source_storage")
        source = create_storage(StorageConfig.model_validate(source_storage))
        self._active = JsonCache(storage=storage)
        self._source = JsonCache(storage=source)

    @classmethod
    def _from_children(cls, active, source):
        instance = cls.__new__(cls)
        instance._active = active
        instance._source = source
        return instance

    async def get(self, key: str) -> Any:
        value = await self._active.get(key)
        if value is not None:
            return value
        value = await self._source.get(key)
        if value is not None:
            await self._active.set(key, value)
        return value

    async def set(self, key: str, value: Any, debug_data: dict | None = None) -> None:
        await self._active.set(key, value, debug_data)

    async def has(self, key: str) -> bool:
        return await self._active.has(key) or await self._source.has(key)

    async def delete(self, key: str) -> None:
        await self._active.delete(key)

    async def clear(self) -> None:
        await self._active.clear()

    def child(self, name: str):
        return self._from_children(
            self._active.child(name),
            self._source.child(name),
        )


def _enable_compacting_cache(config: Any) -> None:
    """Configure GraphRAG to read old cache entries into a clean live cache."""
    from graphrag_cache import CacheConfig, register_cache

    register_cache(COMPACTING_CACHE_TYPE, _CompactingJsonCache)
    config.cache = CacheConfig.model_validate(
        {
            "type": COMPACTING_CACHE_TYPE,
            "storage": {"type": "file", "base_dir": "cache"},
            "source_storage": {"type": "file", "base_dir": "cache_source"},
        }
    )


def _validate_graph_index_outputs(root: Path, document_ids: set[str]) -> None:
    """Reject publication when Parquet or LanceDB lineage is inconsistent."""
    output = root / "output"
    missing = [name for name in PARQUET_FILES if not (output / name).is_file()]
    if missing:
        raise RuntimeError(f"GraphRAG output missing: {', '.join(missing)}")

    tables = {
        name.removesuffix(".parquet"): pd.read_parquet(output / name)
        for name in PARQUET_FILES
    }
    actual_document_ids = set(tables["documents"]["id"].astype(str))
    if actual_document_ids != document_ids:
        raise RuntimeError(
            "GraphRAG documents do not match active project documents: "
            f"expected={sorted(document_ids)} actual={sorted(actual_document_ids)}"
        )
    text_unit_document_ids = set(tables["text_units"]["document_id"].astype(str))
    if not text_unit_document_ids.issubset(document_ids):
        raise RuntimeError(
            "GraphRAG text units reference inactive documents: "
            f"{sorted(text_unit_document_ids - document_ids)}"
        )

    import lancedb

    vector_db = lancedb.connect(output / "lancedb")
    vector_tables = set(vector_db.table_names())
    expected_tables = {
        "entity_description": set(tables["entities"]["id"].astype(str)),
        "text_unit_text": set(tables["text_units"]["id"].astype(str)),
        "community_full_content": set(tables["community_reports"]["id"].astype(str)),
    }
    for table_name, expected_ids in expected_tables.items():
        if table_name not in vector_tables:
            raise RuntimeError(f"GraphRAG vector table missing: {table_name}")
        actual_ids = set(vector_db.open_table(table_name).to_pandas()["id"].astype(str))
        if actual_ids != expected_ids:
            raise RuntimeError(
                f"GraphRAG vector table {table_name} is inconsistent with Parquet"
            )

    graphml_path = output / "graph.graphml"
    if not graphml_path.is_file():
        raise RuntimeError("GraphRAG output missing: graph.graphml")


def _prepare_index_input_documents(
    root: Path,
    documents: list[dict[str, Any]],
    *,
    is_update: bool,
) -> pd.DataFrame:
    """Build a valid full or additive-delta GraphRAG input dataframe.

    Supplying ``input_documents`` makes GraphRAG skip its own update diff.
    Therefore an update must contain only documents absent from the persisted
    baseline; otherwise every existing document is appended again. Older
    FlexSearch full indexes also wrote a null ``human_readable_id``, which
    GraphRAG's incremental merge cannot increment, so repair that baseline in
    the materialized workspace before starting the update.
    """
    frame = pd.DataFrame(documents).copy()
    frame["human_readable_id"] = pd.RangeIndex(len(frame))
    if not is_update:
        return frame

    baseline_path = root / "output" / "documents.parquet"
    baseline = pd.read_parquet(baseline_path)
    numeric_ids = pd.to_numeric(baseline["human_readable_id"], errors="coerce")
    next_id = int(numeric_ids.max()) + 1 if numeric_ids.notna().any() else 0
    for row_index in numeric_ids[numeric_ids.isna()].index:
        numeric_ids.loc[row_index] = next_id
        next_id += 1
    baseline["human_readable_id"] = numeric_ids.astype("int64")
    baseline.to_parquet(baseline_path, index=False)

    existing_ids = set(baseline["id"].astype(str))
    return frame.loc[~frame["id"].astype(str).isin(existing_ids)].copy()


def _graphml_value(value: Any) -> str | int | float | bool:
    """Convert Parquet/Pandas values to GraphML-supported scalar values."""
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        value = value.tolist()
    if isinstance(value, (list, tuple, set)):
        return json.dumps([str(item) for item in value])
    if value is None or (not isinstance(value, (str, bytes)) and pd.isna(value)):
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_graphml_bytes(
    entities: pd.DataFrame,
    relationships: pd.DataFrame,
) -> bytes:
    """Create a GraphML snapshot from the current merged GraphRAG tables."""
    import networkx as nx

    graph = nx.Graph()
    for entity in entities.to_dict(orient="records"):
        title = str(entity.get("title") or "").strip()
        if not title:
            continue
        graph.add_node(
            title,
            entity_id=_graphml_value(entity.get("id")),
            human_readable_id=_graphml_value(entity.get("human_readable_id")),
            type=_graphml_value(entity.get("type")),
            frequency=_graphml_value(entity.get("frequency")),
            degree=_graphml_value(entity.get("degree")),
            description=_graphml_value(entity.get("description")),
            text_unit_ids=_graphml_value(entity.get("text_unit_ids")),
        )

    for relationship in relationships.to_dict(orient="records"):
        source = str(relationship.get("source") or "").strip()
        target = str(relationship.get("target") or "").strip()
        if not source or not target:
            continue
        graph.add_edge(
            source,
            target,
            relationship_id=_graphml_value(relationship.get("id")),
            human_readable_id=_graphml_value(relationship.get("human_readable_id")),
            weight=_graphml_value(relationship.get("weight")),
            combined_degree=_graphml_value(relationship.get("combined_degree")),
            description=_graphml_value(relationship.get("description")),
            text_unit_ids=_graphml_value(relationship.get("text_unit_ids")),
        )

    return "\n".join(nx.generate_graphml(graph, prettyprint=True)).encode("utf-8")


def write_graphml_snapshot(root: Path) -> Path:
    """Regenerate output/graph.graphml from the authoritative Parquet tables."""
    output = root / "output"
    graphml_path = output / "graph.graphml"
    graphml_path.write_bytes(
        build_graphml_bytes(
            pd.read_parquet(output / "entities.parquet"),
            pd.read_parquet(output / "relationships.parquet"),
        )
    )
    return graphml_path


def _set_graphrag_runtime_env() -> None:
    """Expose LLM and embedding credentials for GraphRAG settings.yaml substitutions."""
    embed_ep = graphrag_embedding_endpoint()
    os.environ.setdefault("GRAPHRAG_API_KEY", settings.api_key or "")
    os.environ.setdefault("GRAPHRAG_EMBEDDING_API_KEY", embed_ep.api_key or "")
    os.environ.setdefault("GRAPHRAG_API_BASE", settings.llm_api_base or "")
    os.environ.setdefault(
        "GRAPHRAG_EMBEDDING_API_BASE",
        embed_ep.api_base or "",
    )


def _patch_graphrag_litellm_settings(
    content: str,
    *,
    completion_model_id: str,
    embedding_model_id: str,
) -> str:
    """GraphRAG init templates hardcode model_provider=openai; fix for LiteLLM ids."""
    comp_provider, comp_model = split_litellm_model(completion_model_id)
    embed_provider, embed_model = split_litellm_model(embedding_model_id)
    llm_base = settings.llm_api_base.strip()
    embed_ep = graphrag_embedding_endpoint()
    embed_base_configured = bool(embed_ep.api_base)

    lines = content.splitlines()
    section: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("completion_models:"):
            section = "completion"
            i += 1
            continue
        if line.startswith("embedding_models:"):
            section = "embedding"
            i += 1
            continue
        if line.startswith("input:") or line.startswith("chunking:"):
            section = None
            i += 1
            continue
        if section == "embedding" and line.strip().startswith("api_key:"):
            lines[i] = "    api_key: ${GRAPHRAG_EMBEDDING_API_KEY}"
            if embed_base_configured:
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("api_base:"):
                    lines[i + 1] = "    api_base: ${GRAPHRAG_EMBEDDING_API_BASE}"
                else:
                    lines.insert(i + 1, "    api_base: ${GRAPHRAG_EMBEDDING_API_BASE}")
                    i += 1
            i += 1
            continue
        if section == "completion" and line.strip().startswith("api_key:"):
            if llm_base:
                if i + 1 < len(lines) and lines[i + 1].strip().startswith("api_base:"):
                    lines[i + 1] = "    api_base: ${GRAPHRAG_API_BASE}"
                else:
                    lines.insert(i + 1, "    api_base: ${GRAPHRAG_API_BASE}")
                    i += 1
            i += 1
            continue
        if section == "embedding" and line.strip().startswith("api_base:"):
            if embed_base_configured:
                lines[i] = "    api_base: ${GRAPHRAG_EMBEDDING_API_BASE}"
            i += 1
            continue
        if section == "completion" and line.strip().startswith("api_base:"):
            if llm_base:
                lines[i] = "    api_base: ${GRAPHRAG_API_BASE}"
            i += 1
            continue
        if line.startswith("    model_provider:"):
            if section == "completion":
                lines[i] = f"    model_provider: {comp_provider}"
                if i + 1 < len(lines) and lines[i + 1].startswith("    model:"):
                    lines[i + 1] = f"    model: {comp_model}"
            elif section == "embedding":
                lines[i] = f"    model_provider: {embed_provider}"
                if i + 1 < len(lines) and lines[i + 1].startswith("    model:"):
                    lines[i + 1] = f"    model: {embed_model}"
        i += 1
    return "\n".join(lines) + "\n"


def _patch_graphrag_runtime_settings(content: str) -> str:
    """Tune concurrency and retry behavior for provider rate limits."""
    concurrent = settings.graphrag_concurrent_requests
    if "concurrent_requests:" not in content:
        marker = "embedding_models:"
        idx = content.find(marker)
        if idx == -1:
            content = (
                f"concurrent_requests: {concurrent}\nasync_mode: threaded\n\n{content}"
            )
        else:
            content = (
                content[:idx]
                + f"concurrent_requests: {concurrent}\nasync_mode: threaded\n\n"
                + content[idx:]
            )
    else:
        content = re.sub(
            r"^concurrent_requests:\s*\d+\s*$",
            f"concurrent_requests: {concurrent}",
            content,
            count=1,
            flags=re.MULTILINE,
        )

    retry_block = (
        "    retry:\n"
        "      type: exponential_backoff\n"
        "      max_retries: 12\n"
        "      base_delay: 2.0\n"
        "      max_delay: 120.0"
    )
    content = re.sub(
        r"    retry:\n      type: exponential_backoff\n",
        retry_block + "\n",
        content,
    )
    content = _patch_graphrag_vector_size(
        content, settings.graphrag_embedding_dimension
    )
    return content


def _patch_graphrag_vector_size(content: str, dimension: int) -> str:
    """Set GraphRAG's vector-store size without relying on its 3072 default."""
    lines = content.splitlines()
    vector_store_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == "vector_store:" and not line.startswith((" ", "\t")):
            vector_store_index = index
            continue
        if vector_store_index is None or index <= vector_store_index:
            continue
        if line and not line.startswith((" ", "\t")):
            break
        if line.startswith("  vector_size:"):
            lines[index] = f"  vector_size: {dimension}"
            return "\n".join(lines) + "\n"
    if vector_store_index is None:
        lines.extend(["", "vector_store:", f"  vector_size: {dimension}"])
    else:
        lines.insert(vector_store_index + 1, f"  vector_size: {dimension}")
    return "\n".join(lines) + "\n"


def _configured_graphrag_vector_size(text: str) -> int | None:
    """Read the top-level vector_store.vector_size from persisted YAML text."""
    in_vector_store = False
    for line in text.splitlines():
        if line.strip() == "vector_store:" and not line.startswith((" ", "\t")):
            in_vector_store = True
            continue
        if in_vector_store and line and not line.startswith((" ", "\t")):
            return None
        if in_vector_store and line.startswith("  vector_size:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


# Backward-compatible alias for tests/imports
_patch_graphrag_model_providers = _patch_graphrag_litellm_settings


def _embedding_section_uses_legacy_api_key(text: str) -> bool:
    """True when persisted settings still route embeddings through GRAPHRAG_API_KEY."""
    section: str | None = None
    for line in text.splitlines():
        if line.startswith("completion_models:"):
            section = "completion"
            continue
        if line.startswith("embedding_models:"):
            section = "embedding"
            continue
        if line.startswith("input:") or line.startswith("chunking:"):
            section = None
        if section == "embedding" and "api_key: ${GRAPHRAG_API_KEY}" in line:
            return True
    return False


def _embedding_section_missing_api_base_placeholder(text: str) -> bool:
    """True when a configured base should be in YAML but the placeholder is absent."""
    embed_ep = graphrag_embedding_endpoint()
    if not embed_ep.api_base and not settings.llm_api_base.strip():
        return False
    section: str | None = None
    has_embed_base = False
    has_completion_base = False
    for line in text.splitlines():
        if line.startswith("completion_models:"):
            section = "completion"
            continue
        if line.startswith("embedding_models:"):
            section = "embedding"
            continue
        if line.startswith("input:") or line.startswith("chunking:"):
            section = None
        if section == "embedding" and "${GRAPHRAG_EMBEDDING_API_BASE}" in line:
            has_embed_base = True
        if section == "completion" and "${GRAPHRAG_API_BASE}" in line:
            has_completion_base = True
    if embed_ep.api_base and not has_embed_base:
        return True
    return bool(settings.llm_api_base.strip()) and not has_completion_base


def _needs_config_refresh(root: Path) -> bool:
    """Detect legacy GraphRAG 2.x settings persisted in MinIO."""
    settings_path = root / "settings.yaml"
    if not settings_path.exists():
        return True
    text = settings_path.read_text(encoding="utf-8")
    legacy_markers = (
        "auth_type:",
        "default_vector_store:",
        "cache:\n  type: file\n  base_dir:",
        "model_provider: openai\n    model: gemini/",
    )
    if any(marker in text for marker in legacy_markers):
        return True
    if _embedding_section_uses_legacy_api_key(text):
        return True
    if _embedding_section_missing_api_base_placeholder(text):
        return True
    if "concurrent_requests:" not in text:
        return True
    if _configured_graphrag_vector_size(text) != settings.graphrag_embedding_dimension:
        return True
    prompts = root / "prompts"
    return not prompts.exists() or not any(prompts.iterdir())


class GraphRAGWorkspace:
    """Manage GraphRAG file workspace backed by MinIO."""

    def __init__(self, storage=None) -> None:
        self._storage = storage if storage is not None else get_storage_service()

    def _bootstrap_workspace_sync(self, root: Path, *, force: bool = False) -> None:
        """Write GraphRAG 3.x settings.yaml and prompt files (stdlib loop thread only)."""
        from graphrag.cli.initialize import initialize_project_at

        root.mkdir(parents=True, exist_ok=True)
        settings_path = root / "settings.yaml"
        # A materialized workspace already contains the persisted GraphRAG
        # configuration and prompts. GraphRAG's initializer deliberately
        # rejects an initialized directory unless ``force=True``; calling it
        # again made every incremental rebuild and search fail immediately.
        if settings_path.is_file() and not force:
            return
        if is_local_embedding_model(settings.graphrag_embedding_model):
            raise ValueError(
                "GRAPHRAG_EMBEDDING_MODEL must be a LiteLLM API embedding model "
                "(e.g. gemini/text-embedding-004). Local sentence-transformers "
                "models are supported for vector RAG via EMBEDDING_MODEL only."
            )
        initialize_project_at(
            root,
            force=force,
            model=settings.model_name,
            embedding_model=settings.graphrag_embedding_model,
        )
        content = settings_path.read_text(encoding="utf-8")
        content = _patch_graphrag_litellm_settings(
            content,
            completion_model_id=settings.model_name,
            embedding_model_id=settings.graphrag_embedding_model,
        )
        content = _patch_graphrag_runtime_settings(content)
        if "graphml: false" in content:
            content = content.replace("graphml: false", "graphml: true")
        settings_path.write_text(content, encoding="utf-8")

    def bootstrap_workspace(self, root: Path, *, force: bool = False) -> None:
        """Sync bootstrap for tests; production code should use materialize()."""
        self._bootstrap_workspace_sync(root, force=force)

    def sync_from_minio(
        self, project_id: UUID | str, generation: int, root: Path
    ) -> bool:
        prefix = graphrag_storage_prefix(project_id, generation)
        if not self._storage.list_files(prefix):
            return False
        self._storage.download_prefix(prefix, str(root))
        return True

    def sync_to_minio(
        self,
        project_id: UUID | str,
        generation: int,
        root: Path,
        *,
        replace_index_artifacts: bool = False,
        replace_cache: bool = False,
    ) -> None:
        prefix = graphrag_storage_prefix(project_id, generation)
        if replace_index_artifacts:
            for sub in FULL_REBUILD_REPLACED_SUBDIRS:
                self._storage.delete_prefix(f"{prefix}/{sub}")

        for sub in ("input", "output", "prompts", "logs"):
            sub_path = root / sub
            if sub_path.exists():
                self._storage.upload_directory(str(sub_path), f"{prefix}/{sub}")
        settings_file = root / "settings.yaml"
        if settings_file.exists():
            self._storage.upload_file(
                f"{prefix}/settings.yaml",
                settings_file.read_bytes(),
                content_type="application/x-yaml",
            )

        # Cache replacement is deliberately last. Retrieval never depends on
        # cache, and a publication failure before this point leaves the old
        # cache available for a retry.
        cache_path = root / "cache"
        if replace_cache:
            self._storage.delete_prefix(f"{prefix}/cache")
        if cache_path.exists():
            self._storage.upload_directory(str(cache_path), f"{prefix}/cache")

    def clear_index_for_empty_project(
        self, project_id: UUID | str, generation: int
    ) -> None:
        """Remove all document-derived artifacts when no active documents remain."""
        prefix = graphrag_storage_prefix(project_id, generation)
        for sub in (*FULL_REBUILD_REPLACED_SUBDIRS, "cache"):
            self._storage.delete_prefix(f"{prefix}/{sub}")

    async def materialize(self, project_id: UUID | str, generation: int) -> Path:
        root = Path(tempfile.mkdtemp(prefix=f"graphrag-{project_id}-"))
        synced = self.sync_from_minio(project_id, generation, root)
        force = not synced or _needs_config_refresh(root)
        logger.info(
            "Materializing GraphRAG workspace for project %s (synced=%s, force=%s)",
            project_id,
            synced,
            force,
        )
        await run_sync_in_std_thread(
            lambda: self._bootstrap_workspace_sync(root, force=force)
        )
        return root

    def cleanup(self, root: Path) -> None:
        shutil.rmtree(root, ignore_errors=True)

    def load_parquet_tables(self, root: Path) -> dict[str, pd.DataFrame]:
        output = root / "output"
        tables: dict[str, pd.DataFrame] = {}
        for name in PARQUET_FILES:
            path = output / name
            if path.exists():
                tables[name.replace(".parquet", "")] = pd.read_parquet(path)
        return tables

    def find_graphml_path(self, root: Path) -> Path | None:
        output = root / "output"
        for candidate in GRAPHML_CANDIDATES:
            path = output / candidate
            if path.exists():
                return path
        for path in output.rglob("*.graphml"):
            return path
        return None

    async def build_index_for_project(
        self,
        project_id: UUID,
        *,
        is_update: bool = False,
        force_full_rebuild: bool = False,
        compact_cache: bool = False,
        manage_in_flight: bool = True,
        generation: int | None = None,
    ) -> None:
        if not settings.graph_indexing_enabled:
            logger.info("Graph indexing disabled globally; skip project %s", project_id)
            return

        if manage_in_flight:
            from app.services.distributed_lock import project_graph_lease

            async with async_session_maker() as db:
                project = await _get_project(db, project_id)
                expected_generation = generation or project.rag_generation
            async with project_graph_lease(
                str(project_id), expected_generation
            ) as acquired:
                if not acquired:
                    logger.info("GraphRAG build coalesced for %s", project_id)
                    return
                await self.build_index_for_project(
                    project_id,
                    is_update=is_update,
                    force_full_rebuild=force_full_rebuild,
                    compact_cache=compact_cache,
                    manage_in_flight=False,
                    generation=expected_generation,
                )
                return

        root: Path | None = None
        start_ts = time.monotonic()
        expected_generation = generation or 0
        try:
            async with async_session_maker() as db:
                project = await _get_project(db, project_id)
                expected_generation = generation or project.rag_generation
                if expected_generation != project.rag_generation:
                    return
                if project.rag_mode != RagMode.GRAPH:
                    return
                rag_config = AppGraphRagConfig.from_db(project.rag_config)
                if rag_config.graph_backend != "microsoft":
                    return
                if not rag_config.microsoft_indexing.enabled:
                    state = GraphIndexState(backend="microsoft", status="disabled")
                    project.graph_index_status = state.to_db()
                    await db.commit()
                    logger.info(
                        "Graph index status -> disabled for project %s", project_id
                    )
                    return

                previous = GraphIndexState.from_db(project.graph_index_status)
                state = GraphIndexState(
                    backend="microsoft",
                    status="indexing",
                    indexed_at=previous.indexed_at,
                    fingerprint=rag_config.graph_indexing_fingerprint(),
                    indexing_started_at=datetime.now(timezone.utc),
                    error=None,
                    document_count=previous.document_count,
                    entity_count=previous.entity_count,
                    passage_count=previous.passage_count,
                )
                project.graph_index_status = state.to_db()
                await db.commit()
                logger.info(
                    "Graph index status -> indexing for project %s (fingerprint=%s)",
                    project_id,
                    state.fingerprint,
                )

                docs = await _load_extracted_documents(db, project_id)
                if not docs:
                    await run_sync_in_std_thread(
                        lambda: self.clear_index_for_empty_project(
                            project_id, expected_generation
                        )
                    )
                    state = GraphIndexState(
                        backend="microsoft",
                        status="pending",
                        fingerprint=rag_config.graph_indexing_fingerprint(),
                        document_count=0,
                    )
                    project.graph_index_status = state.to_db()
                    await db.commit()
                    logger.info(
                        "Graph index status -> pending for project %s (no extracted docs)",
                        project_id,
                    )
                    return

            logger.info(
                "Starting GraphRAG index build for project %s (%s docs, method=%s, update=%s)",
                project_id,
                len(docs),
                rag_config.microsoft_indexing.method,
                is_update,
            )
            root = await self.materialize(project_id, expected_generation)
            active_ids = {str(document["id"]) for document in docs}
            deleted_ids, changed_ids = _destructive_graph_changes(root, docs)
            if deleted_ids or changed_ids:
                force_full_rebuild = True
                compact_cache = True
                logger.info(
                    "GraphRAG baseline differs for project %s "
                    "(%s deleted, %s changed); "
                    "forcing full rebuild with cache compaction",
                    project_id,
                    len(deleted_ids),
                    len(changed_ids),
                )
            requested_update = is_update
            is_update = (
                is_update
                and not force_full_rebuild
                and _has_incremental_update_baseline(root)
            )
            if requested_update and not is_update:
                logger.info(
                    "GraphRAG update requested for project %s but a clean full "
                    "build is required",
                    project_id,
                )
            if not is_update:
                _prepare_full_rebuild_workspace(root)
            if compact_cache:
                _prepare_cache_compaction_workspace(root)
            all_documents_df = pd.DataFrame(docs)
            df = _prepare_index_input_documents(
                root,
                docs,
                is_update=is_update,
            )
            if is_update:
                logger.info(
                    "GraphRAG additive delta for project %s: %s new of %s total documents",
                    project_id,
                    len(df),
                    len(all_documents_df),
                )
                if df.empty:
                    async with async_session_maker() as db:
                        project = await _get_project(db, project_id)
                        previous = GraphIndexState.from_db(project.graph_index_status)
                        project.graph_index_status = GraphIndexState(
                            backend="microsoft",
                            status="ready",
                            indexed_at=previous.indexed_at,
                            fingerprint=previous.fingerprint,
                            document_count=previous.document_count,
                            entity_count=previous.entity_count,
                            passage_count=previous.passage_count,
                        ).to_db()
                        await db.commit()
                    logger.info(
                        "GraphRAG manual rebuild found no source changes for project %s",
                        project_id,
                    )
                    return
            root_path = root
            method_name = rag_config.microsoft_indexing.method

            async def _build() -> list:
                from graphrag.api import build_index
                from graphrag.config.load_config import load_config

                install_graphrag_failfast()
                install_graphrag_rate_limit_retry()
                _set_graphrag_runtime_env()
                config = load_config(root_path)
                if compact_cache:
                    _enable_compacting_cache(config)
                method = _indexing_method_for(method_name)
                logger.info(
                    "GraphRAG build_index called for project %s (method=%s, is_update=%s)",
                    project_id,
                    getattr(method, "value", method),
                    is_update,
                )
                return await build_index(
                    config=config,
                    method=method,
                    is_update_run=is_update,
                    callbacks=_build_workflow_callbacks(),
                    input_documents=df,
                    verbose=False,
                )

            results = await run_in_std_event_loop(_build)
            errors = [r.error for r in results if getattr(r, "error", None)]
            if errors:
                raise RuntimeError("; ".join(str(e) for e in errors if e))

            graphml_path = write_graphml_snapshot(root)
            logger.info(
                "Regenerated GraphML snapshot for project %s at %s",
                project_id,
                graphml_path,
            )

            input_csv = root / "input" / "documents.csv"
            input_csv.parent.mkdir(parents=True, exist_ok=True)
            all_documents_df.to_csv(input_csv, index=False)

            _validate_graph_index_outputs(root, active_ids)
            logger.info(
                "Validated GraphRAG output lineage for project %s (%s documents)",
                project_id,
                len(active_ids),
            )

            async with async_session_maker() as db:
                current = await _get_project(db, project_id)
                if current.rag_generation != expected_generation:
                    logger.info(
                        "Discarding stale GraphRAG generation %s", expected_generation
                    )
                    return
                if not await _source_snapshot_matches(db, project_id, docs):
                    from app.services.graph_index_tasks import (
                        mark_microsoft_graph_index_dirty,
                    )

                    mark_microsoft_graph_index_dirty(current)
                    await db.commit()
                    logger.info(
                        "Discarding GraphRAG build for project %s because source "
                        "documents changed during indexing",
                        project_id,
                    )
                    return
            self.sync_to_minio(
                project_id,
                expected_generation,
                root,
                replace_index_artifacts=not is_update,
                replace_cache=compact_cache,
            )

            async with async_session_maker() as db:
                project = await _get_project(db, project_id)
                if project.rag_generation != expected_generation:
                    return
                if not await _source_snapshot_matches(db, project_id, docs):
                    from app.services.graph_index_tasks import (
                        mark_microsoft_graph_index_dirty,
                    )

                    mark_microsoft_graph_index_dirty(project)
                    await db.commit()
                    logger.info(
                        "Published GraphRAG build for project %s became stale because "
                        "source documents changed during publication",
                        project_id,
                    )
                    return
                project.graph_index_status = GraphIndexState(
                    backend="microsoft",
                    status="ready",
                    indexed_at=datetime.now(timezone.utc),
                    fingerprint=rag_config.graph_indexing_fingerprint(),
                    error=None,
                    document_count=len(docs),
                ).to_db()
                await db.commit()
            elapsed = time.monotonic() - start_ts
            logger.info(
                "Graph index status -> ready for project %s (%s docs, elapsed=%.1fs)",
                project_id,
                len(docs),
                elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start_ts
            logger.error(
                "Graph index failed for project %s after %.1fs: %s",
                project_id,
                elapsed,
                exc,
            )
            async with async_session_maker() as db:
                project = await _get_project(db, project_id)
                if project.rag_generation != expected_generation:
                    return
                prev = GraphIndexState.from_db(project.graph_index_status)
                project.graph_index_status = GraphIndexState(
                    backend="microsoft",
                    status="failed",
                    indexed_at=prev.indexed_at,
                    fingerprint=prev.fingerprint,
                    error=str(exc),
                    document_count=prev.document_count,
                ).to_db()
                await db.commit()
            logger.info("Graph index status -> failed for project %s", project_id)
            raise
        finally:
            if root is not None:
                self.cleanup(root)

    async def run_local_search(
        self,
        project_id: str,
        query: str,
        *,
        community_level: int,
        top_k: int,
        generation: int,
    ) -> list[Any]:
        root = await self.materialize(project_id, generation)
        try:
            root_path = root
            tables = self.load_parquet_tables(root)
            required = (
                "entities",
                "communities",
                "community_reports",
                "text_units",
                "relationships",
            )
            missing = [k for k in required if k not in tables]
            if missing:
                raise FileNotFoundError(
                    f"Graph index missing tables: {', '.join(missing)}"
                )

            async def _search() -> tuple[Any, list[Any]]:
                from graphrag.api import local_search
                from graphrag.config.load_config import load_config

                _set_graphrag_runtime_env()
                config = load_config(root_path)
                return await local_search(
                    config=config,
                    entities=tables["entities"],
                    communities=tables["communities"],
                    community_reports=tables["community_reports"],
                    text_units=tables["text_units"],
                    relationships=tables["relationships"],
                    covariates=tables.get("covariates"),
                    community_level=community_level,
                    response_type="multiple paragraphs",
                    query=query,
                )

            _response, context = await run_in_std_event_loop(_search)
            return context  # type: ignore[return-value]
        finally:
            self.cleanup(root)

    async def run_global_search(
        self,
        project_id: str,
        query: str,
        *,
        community_level: int,
        dynamic_community_selection: bool,
        top_k: int,
        generation: int,
    ) -> list[Any]:
        root = await self.materialize(project_id, generation)
        try:
            root_path = root
            tables = self.load_parquet_tables(root)
            required = ("entities", "communities", "community_reports")
            missing = [k for k in required if k not in tables]
            if missing:
                raise FileNotFoundError(
                    f"Graph index missing tables: {', '.join(missing)}"
                )

            async def _search() -> tuple[Any, list[Any]]:
                from graphrag.api import global_search
                from graphrag.config.load_config import load_config

                _set_graphrag_runtime_env()
                config = load_config(root_path)
                return await global_search(
                    config=config,
                    entities=tables["entities"],
                    communities=tables["communities"],
                    community_reports=tables["community_reports"],
                    community_level=community_level,
                    dynamic_community_selection=dynamic_community_selection,
                    response_type="multiple paragraphs",
                    query=query,
                )

            _response, context = await run_in_std_event_loop(_search)
            return context  # type: ignore[return-value]
        finally:
            self.cleanup(root)


async def _get_project(db: AsyncSession, project_id: UUID) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    return project


async def _load_extracted_documents(
    db: AsyncSession,
    project_id: UUID,
) -> list[dict[str, str]]:
    storage = get_storage_service()
    result = await db.execute(
        select(Document).where(
            Document.project_id == project_id,
            Document.status == DocumentStatus.COMPLETED,
            Document.extracted_text_path.isnot(None),
        )
    )
    documents = result.scalars().all()
    rows: list[dict[str, str]] = []
    for doc in documents:
        path = doc.extracted_text_path or extracted_md_key(project_id, doc.id)
        if not storage.file_exists(path):
            continue
        text = storage.download_file(path).decode("utf-8").strip()
        if not text:
            continue
        rows.append(
            {
                "id": str(doc.id),
                "title": doc.filename,
                "text": text,
                "document_id": str(doc.id),
            }
        )
    return rows


async def _source_snapshot_matches(
    db: AsyncSession,
    project_id: UUID,
    expected_documents: list[dict[str, str]],
) -> bool:
    """Return false if uploads or deletions occurred during a manual build."""
    from sqlalchemy import func

    pending_result = await db.execute(
        select(func.count())
        .select_from(Document)
        .where(
            Document.project_id == project_id,
            Document.status.notin_((DocumentStatus.COMPLETED, DocumentStatus.FAILED)),
        )
    )
    if int(pending_result.scalar() or 0):
        return False
    current_documents = await _load_extracted_documents(db, project_id)
    expected = {
        str(document["id"]): (document["title"], document["text"])
        for document in expected_documents
    }
    current = {
        str(document["id"]): (document["title"], document["text"])
        for document in current_documents
    }
    return current == expected


_workspace: GraphRAGWorkspace | None = None


def get_graphrag_workspace() -> GraphRAGWorkspace:
    global _workspace
    if _workspace is None:
        _workspace = GraphRAGWorkspace()
    return _workspace
