"""Microsoft GraphRAG workspace: MinIO sync, indexing, and search materialization."""

from __future__ import annotations

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


def graphrag_storage_prefix(project_id: UUID | str, generation: int) -> str:
    return f"projects/{project_id}/graphrag/generations/{generation}"


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
    return content


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
        settings_path = root / "settings.yaml"
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
        self, project_id: UUID | str, generation: int, root: Path
    ) -> None:
        prefix = graphrag_storage_prefix(project_id, generation)
        for sub in ("input", "output", "cache", "prompts", "logs"):
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

                state = GraphIndexState(
                    backend="microsoft",
                    status="indexing",
                    fingerprint=rag_config.graph_indexing_fingerprint(),
                    indexing_started_at=datetime.now(timezone.utc),
                    error=None,
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
            df = pd.DataFrame(docs)
            root_path = root
            method_name = rag_config.microsoft_indexing.method

            async def _build() -> list:
                from graphrag.api import build_index
                from graphrag.config.load_config import load_config

                install_graphrag_failfast()
                install_graphrag_rate_limit_retry()
                _set_graphrag_runtime_env()
                config = load_config(root_path)
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

            input_csv = root / "input" / "documents.csv"
            input_csv.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(input_csv, index=False)

            async with async_session_maker() as db:
                current = await _get_project(db, project_id)
                if current.rag_generation != expected_generation:
                    logger.info(
                        "Discarding stale GraphRAG generation %s", expected_generation
                    )
                    return
            self.sync_to_minio(project_id, expected_generation, root)

            async with async_session_maker() as db:
                project = await _get_project(db, project_id)
                if project.rag_generation != expected_generation:
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


_workspace: GraphRAGWorkspace | None = None


def get_graphrag_workspace() -> GraphRAGWorkspace:
    global _workspace
    if _workspace is None:
        _workspace = GraphRAGWorkspace()
    return _workspace
