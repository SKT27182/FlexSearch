"""Microsoft GraphRAG workspace: MinIO sync, indexing, and search materialization."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
from graphrag.api import build_index, global_search, local_search
from graphrag.config.enums import IndexingMethod
from graphrag.config.load_config import load_config
from graphrag.config.models.graph_rag_config import GraphRagConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Document, DocumentStatus, Project, RagMode
from app.db.postgres import async_session_maker
from app.schemas.graph_index import GraphIndexState
from app.schemas.rag_config import GraphRagConfig
from app.services.document_storage import extracted_md_key
from app.services.storage import get_storage_service
from app.utils.logger import create_logger

logger = create_logger(__name__)

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


def graphrag_storage_prefix(project_id: UUID | str) -> str:
    return f"projects/{project_id}/graphrag"


def _settings_yaml_template() -> str:
    model = settings.model_name
    api_key = settings.api_key or "${GRAPHRAG_API_KEY}"
    return f"""completion_models:
  default_completion_model:
    type: chat
    model_provider: openai
    auth_type: api_key
    model: {model}
    api_key: {api_key}
embedding_models:
  default_embedding_model:
    type: embedding
    model_provider: openai
    auth_type: api_key
    model: text-embedding-3-small
    api_key: {api_key}
input_storage:
  type: file
  base_dir: input
output_storage:
  type: file
  base_dir: output
cache:
  type: file
  base_dir: cache
vector_store:
  default_vector_store:
    type: lancedb
    db_uri: output/lancedb
snapshots:
  graphml: true
  embeddings: false
  raw_graph: false
input:
  type: csv
  file_pattern: ".*\\.csv$"
  id_column: id
  title_column: title
  text_column: text
"""


class GraphRAGWorkspace:
    """Manage GraphRAG file workspace backed by MinIO."""

    def __init__(self) -> None:
        self._storage = get_storage_service()

    def write_settings(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "input").mkdir(exist_ok=True)
        (root / "output").mkdir(exist_ok=True)
        (root / "cache").mkdir(exist_ok=True)
        settings_path = root / "settings.yaml"
        settings_path.write_text(_settings_yaml_template(), encoding="utf-8")

    def load_config(self, root: Path) -> GraphRagConfig:
        os.environ.setdefault("GRAPHRAG_API_KEY", settings.api_key or "")
        return load_config(root)

    def sync_from_minio(self, project_id: UUID | str, root: Path) -> bool:
        prefix = graphrag_storage_prefix(project_id)
        if not self._storage.list_files(prefix):
            return False
        self.write_settings(root)
        self._storage.download_prefix(prefix, str(root))
        return True

    def sync_to_minio(self, project_id: UUID | str, root: Path) -> None:
        prefix = graphrag_storage_prefix(project_id)
        for sub in ("input", "output", "cache"):
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

    def materialize(self, project_id: UUID | str) -> Path:
        root = Path(tempfile.mkdtemp(prefix=f"graphrag-{project_id}-"))
        if not self.sync_from_minio(project_id, root):
            self.write_settings(root)
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
    ) -> None:
        if not settings.graph_indexing_enabled:
            logger.info("Graph indexing disabled globally; skip project %s", project_id)
            return

        async with async_session_maker() as db:
            project = await _get_project(db, project_id)
            if project.rag_mode != RagMode.GRAPH:
                return
            rag_config = GraphRagConfig.from_db(project.rag_config)
            if rag_config.graph_backend != "microsoft":
                return
            if not rag_config.microsoft_indexing.enabled:
                state = GraphIndexState(backend="microsoft", status="disabled")
                project.graph_index_status = state.to_db()
                await db.commit()
                return

            state = GraphIndexState(
                backend="microsoft",
                status="indexing",
                fingerprint=rag_config.graph_indexing_fingerprint(),
                error=None,
            )
            project.graph_index_status = state.to_db()
            await db.commit()

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
                return

        root = self.materialize(project_id)
        try:
            df = pd.DataFrame(docs)
            config = self.load_config(root)
            method = (
                IndexingMethod.NLP
                if rag_config.microsoft_indexing.method == "nlp"
                else IndexingMethod.Standard
            )
            results = await build_index(
                config=config,
                method=method,
                is_update_run=is_update,
                input_documents=df,
                verbose=False,
            )
            errors = [r.error for r in results if getattr(r, "error", None)]
            if errors:
                raise RuntimeError("; ".join(str(e) for e in errors if e))

            input_csv = root / "input" / "documents.csv"
            input_csv.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(input_csv, index=False)

            self.sync_to_minio(project_id, root)

            async with async_session_maker() as db:
                project = await _get_project(db, project_id)
                project.graph_index_status = GraphIndexState(
                    backend="microsoft",
                    status="ready",
                    indexed_at=datetime.now(timezone.utc),
                    fingerprint=rag_config.graph_indexing_fingerprint(),
                    error=None,
                    document_count=len(docs),
                ).to_db()
                await db.commit()
            logger.info("Graph index ready for project %s (%s docs)", project_id, len(docs))
        except Exception as exc:
            logger.exception("Graph index failed for project %s", project_id)
            async with async_session_maker() as db:
                project = await _get_project(db, project_id)
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
            raise
        finally:
            self.cleanup(root)

    async def run_local_search(
        self,
        project_id: str,
        query: str,
        *,
        community_level: int,
        top_k: int,
    ) -> list[Any]:
        root = self.materialize(project_id)
        try:
            config = self.load_config(root)
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
            _response, context = await local_search(
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
    ) -> list[Any]:
        root = self.materialize(project_id)
        try:
            config = self.load_config(root)
            tables = self.load_parquet_tables(root)
            required = ("entities", "communities", "community_reports")
            missing = [k for k in required if k not in tables]
            if missing:
                raise FileNotFoundError(
                    f"Graph index missing tables: {', '.join(missing)}"
                )
            _response, context = await global_search(
                config=config,
                entities=tables["entities"],
                communities=tables["communities"],
                community_reports=tables["community_reports"],
                community_level=community_level,
                dynamic_community_selection=dynamic_community_selection,
                response_type="multiple paragraphs",
                query=query,
            )
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
