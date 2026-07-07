"""Tests for the GraphRAG "NLP" crash fix, startup reconciliation, and the
wait-for-all-documents scheduling gate."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus, Project, RagMode
from app.schemas.graph_index import GraphIndexState
from app.services import graph_index_tasks as tasks
from app.services import graphrag_workspace as gw


def test_indexing_method_maps_nlp_to_fast() -> None:
    from graphrag.config.enums import IndexingMethod

    assert gw._indexing_method_for("nlp") is IndexingMethod.Fast
    assert gw._indexing_method_for("fast") is IndexingMethod.Fast


def test_indexing_method_maps_standard_to_standard() -> None:
    from graphrag.config.enums import IndexingMethod

    assert gw._indexing_method_for("standard") is IndexingMethod.Standard
    assert gw._indexing_method_for("std") is IndexingMethod.Standard


def test_indexing_method_falls_back_for_unknown() -> None:
    from graphrag.config.enums import IndexingMethod

    assert gw._indexing_method_for("totally-unknown") is IndexingMethod.Standard
    assert gw._indexing_method_for("") is IndexingMethod.Standard
    assert gw._indexing_method_for(None) is IndexingMethod.Standard


def test_concurrency_guard_acquires_and_releases() -> None:
    pid = uuid4()
    # Clean state
    tasks._in_flight.discard(str(pid))
    assert not tasks.is_graph_index_in_flight(pid)
    assert tasks._acquire_in_flight(pid) is True
    assert tasks.is_graph_index_in_flight(pid)
    # Second acquire fails (already in flight)
    assert tasks._acquire_in_flight(pid) is False
    tasks._release_in_flight(pid)
    assert not tasks.is_graph_index_in_flight(pid)


@pytest.mark.asyncio
async def test_reconcile_interrupted_graph_indexes_marks_indexing_as_failed(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GRAPH project left at 'indexing' is reset to 'failed' on startup."""
    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)

    project = Project(
        id=pid,
        name="Stuck Graph",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
        graph_index_status=GraphIndexState(
            backend="microsoft", status="indexing"
        ).to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    count = await tasks.reconcile_interrupted_graph_indexes()

    assert count == 1
    await db_session.refresh(project)
    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "failed"
    assert state.backend == "microsoft"
    assert "interrupted" in (state.error or "").lower()


@pytest.mark.asyncio
async def test_reconcile_leaves_non_indexing_statuses_alone(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)

    project = Project(
        id=pid,
        name="Ready Graph",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
        graph_index_status=GraphIndexState(
            backend="microsoft", status="ready"
        ).to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    count = await tasks.reconcile_interrupted_graph_indexes()

    assert count == 0
    await db_session.refresh(project)
    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "ready"


@pytest.mark.asyncio
async def test_count_non_terminal_documents(db_session: AsyncSession) -> None:
    from app.services.document_worker import _count_non_terminal_documents

    pid = uuid4()
    project = Project(
        id=pid,
        name="Docs Project",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
    )
    db_session.add(project)
    await db_session.flush()

    statuses = [
        DocumentStatus.COMPLETED,
        DocumentStatus.COMPLETED,
        DocumentStatus.EXTRACTING,
        DocumentStatus.UPLOADED,
        DocumentStatus.FAILED,
    ]
    for i, st in enumerate(statuses):
        db_session.add(
            Document(
                id=uuid4(),
                project_id=pid,
                filename=f"doc-{i}.pdf",
                content_type="application/pdf",
                storage_path=f"p/{pid}/doc-{i}",
                file_size=1024,
                status=st,
            )
        )
    await db_session.commit()

    # Non-terminal: EXTRACTING, UPLOADED => 2
    pending = await _count_non_terminal_documents(db_session, pid)
    assert pending == 2


@pytest.mark.asyncio
async def test_graph_extractor_failfast_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    from graphrag.index.operations.extract_graph.graph_extractor import GraphExtractor

    from app.services import graphrag_failfast as ff

    monkeypatch.setattr(ff, "_INSTALLED", False)
    GraphExtractor._flexsearch_failfast = False  # type: ignore[attr-defined]
    ff.install_graphrag_failfast()

    class _FakeModel:
        async def completion_async(self, messages):
            raise RuntimeError("auth failed")

    extractor = GraphExtractor(
        model=_FakeModel(),
        prompt="Entity_types: {entity_types}\nText: {input_text}\nOutput:",
        max_gleanings=0,
    )

    with pytest.raises(RuntimeError, match="auth failed"):
        await extractor("sample text", ["person"], "doc-1")


@pytest.mark.asyncio
async def test_derive_from_rows_failfast_stops_on_first_error() -> None:
    import pandas as pd

    from graphrag.callbacks.noop_workflow_callbacks import NoopWorkflowCallbacks
    import graphrag.index.utils.derive_from_rows as dfr

    from app.services import graphrag_failfast as ff

    ff._INSTALLED = False
    dfr._flexsearch_failfast = False  # type: ignore[attr-defined]
    ff.install_graphrag_failfast()

    calls = {"n": 0}

    async def transform(_row: pd.Series) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("first chunk failed")
        return "ok"

    df = pd.DataFrame({"id": ["a", "b", "c"], "text": ["1", "2", "3"]})

    with pytest.raises(ValueError, match="first chunk failed"):
        await dfr.derive_from_rows(
            df,
            transform,
            NoopWorkflowCallbacks(),
            num_threads=1,
        )


def test_patch_graphrag_init_loggers_routes_to_root(tmp_path, monkeypatch) -> None:
    import logging

    import graphrag.logger.standard_logging as standard_logging

    from app.utils.logging_bridge import (
        backend_log_path,
        patch_graphrag_init_loggers,
        setup_unified_logging,
    )

    log_file = tmp_path / "backend.log"
    monkeypatch.setenv("BACKEND_LOG_FILE", str(log_file))
    monkeypatch.setattr(standard_logging, "_flexsearch_patched", False)

    setup_unified_logging("info")
    patch_graphrag_init_loggers()

    standard_logging.init_loggers(config=None, verbose=False)

    gr = logging.getLogger("graphrag")
    assert gr.propagate is True
    assert gr.handlers == []
    assert backend_log_path() == log_file
