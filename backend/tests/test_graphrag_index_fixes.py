"""Tests for the GraphRAG "NLP" crash fix, startup reconciliation, and the
wait-for-all-documents scheduling gate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentStatus, OutboxEvent, Project, RagMode
from app.schemas.graph_index import GraphIndexState
from app.schemas.rag_config import GraphRagConfig
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


def test_mark_microsoft_graph_dirty_preserves_published_index() -> None:
    project = Project(
        id=uuid4(),
        name="Dirty Graph",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
        graph_index_status=GraphIndexState(
            backend="microsoft",
            status="ready",
            indexed_at="2026-07-24T00:00:00Z",
            document_count=2,
        ).to_db(),
    )

    tasks.mark_microsoft_graph_index_dirty(project)

    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "stale"
    assert state.indexed_at is not None
    assert state.document_count == 2


def test_mark_new_microsoft_graph_dirty_stays_pending() -> None:
    project = Project(
        id=uuid4(),
        name="New Graph",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
        graph_index_status=GraphIndexState(
            backend="microsoft", status="pending"
        ).to_db(),
    )

    tasks.mark_microsoft_graph_index_dirty(project)

    assert GraphIndexState.from_db(project.graph_index_status).status == "pending"


@pytest.mark.asyncio
async def test_microsoft_upload_marks_graph_dirty_without_scheduling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import document_worker

    project = Project(
        id=uuid4(),
        name="Manual Refresh",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
        graph_index_status=GraphIndexState(
            backend="microsoft",
            status="ready",
            indexed_at="2026-07-24T00:00:00Z",
        ).to_db(),
    )
    document = Document(
        id=uuid4(),
        project_id=project.id,
        filename="new.pdf",
        content_type="application/pdf",
        storage_path="new/raw.pdf",
        file_size=1,
    )
    db = AsyncMock()
    complete = AsyncMock()
    monkeypatch.setattr(document_worker, "_complete_graph_document", complete)

    await document_worker._handle_graph_after_extract(
        db,
        document,
        project,
        GraphRagConfig(graph_backend="microsoft"),
        MagicMock(),
        "hash",
    )

    complete.assert_awaited_once_with(db, document)
    db.commit.assert_awaited_once()
    assert GraphIndexState.from_db(project.graph_index_status).status == "stale"


@pytest.mark.asyncio
async def test_microsoft_delete_marks_graph_dirty_without_rebuilding(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import document_tasks, outbox, summary_tasks

    project = Project(
        id=uuid4(),
        name="Manual Delete Refresh",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
        graph_index_status=GraphIndexState(
            backend="microsoft",
            status="ready",
            indexed_at="2026-07-24T00:00:00Z",
            document_count=2,
        ).to_db(),
    )
    document = Document(
        id=uuid4(),
        project_id=project.id,
        filename="delete.pdf",
        content_type="application/pdf",
        storage_path="delete/raw.pdf",
        file_size=1,
        status=DocumentStatus.DELETING,
    )
    db_session.add_all([project, document])
    await db_session.commit()

    storage = MagicMock()
    storage.file_exists.return_value = False
    monkeypatch.setattr(outbox, "get_storage_service", lambda: storage)
    monkeypatch.setattr(document_tasks, "cancel_document_ingest", MagicMock())
    monkeypatch.setattr(summary_tasks, "cancel_document_summary", MagicMock())
    event = OutboxEvent(
        event_type="cleanup_document",
        aggregate_type="document",
        aggregate_id=document.id,
        project_id=project.id,
        payload={},
    )

    await outbox._dispatch_event(db_session, event)
    await db_session.flush()

    assert await db_session.get(Document, document.id) is None
    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "stale"
    assert state.document_count == 2


@pytest.mark.asyncio
async def test_reconcile_interrupted_graph_indexes_marks_indexing_as_failed(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GRAPH project left at 'indexing' with a dead task is reset on startup."""
    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)
    monkeypatch.setattr(tasks, "is_graph_rebuild_alive", lambda _pid: False)

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
async def test_reconcile_interrupted_leaves_live_celery_build_alone(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Startup reconcile must not fail projects whose Celery rebuild is alive."""
    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)
    monkeypatch.setattr(tasks, "is_graph_rebuild_alive", lambda _pid: True)

    project = Project(
        id=pid,
        name="Live Graph Build",
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

    assert count == 0
    await db_session.refresh(project)
    assert GraphIndexState.from_db(project.graph_index_status).status == "indexing"


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
        graph_index_status=GraphIndexState(backend="microsoft", status="ready").to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    count = await tasks.reconcile_interrupted_graph_indexes()

    assert count == 0
    await db_session.refresh(project)
    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "ready"


@pytest.mark.asyncio
async def test_reconcile_stale_graph_index_when_celery_task_dead(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status poll recovers stuck 'indexing' without API restart when task is dead."""
    from datetime import datetime, timezone

    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)
    monkeypatch.setattr(tasks, "is_graph_rebuild_alive", lambda _pid: False)

    project = Project(
        id=pid,
        name="Stuck Mid-Build",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
        graph_index_status=GraphIndexState(
            backend="microsoft",
            status="indexing",
            indexing_started_at=datetime.now(timezone.utc),
        ).to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    assert await tasks.reconcile_stale_graph_index(pid) is True
    await db_session.refresh(project)
    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "failed"
    assert "no longer running" in (state.error or "").lower()


@pytest.mark.asyncio
async def test_reconcile_stale_leaves_alive_indexing_alone(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)
    monkeypatch.setattr(tasks, "is_graph_rebuild_alive", lambda _pid: True)

    project = Project(
        id=pid,
        name="Active Build",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "microsoft"},
        graph_index_status=GraphIndexState(
            backend="microsoft",
            status="indexing",
            indexing_started_at=datetime.now(timezone.utc),
        ).to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    assert await tasks.reconcile_stale_graph_index(pid) is False
    await db_session.refresh(project)
    assert GraphIndexState.from_db(project.graph_index_status).status == "indexing"


@pytest.mark.asyncio
async def test_reconcile_stale_neo4j_when_ingest_tasks_dead(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neo4j path recovers promptly via ingest-task alive check, not only timeout."""
    from datetime import datetime, timezone

    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    async def _neo4j_dead(_pid, *, db=None):
        return False

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)
    monkeypatch.setattr(tasks, "is_neo4j_graph_indexing_alive", _neo4j_dead)

    project = Project(
        id=pid,
        name="Stuck Neo4j",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "neo4j"},
        graph_index_status=GraphIndexState(
            backend="neo4j",
            status="indexing",
            indexing_started_at=datetime.now(timezone.utc),
        ).to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    assert await tasks.reconcile_stale_graph_index(pid) is True
    await db_session.refresh(project)
    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "failed"
    assert "no longer running" in (state.error or "").lower()


@pytest.mark.asyncio
async def test_reconcile_stale_neo4j_leaves_alive_ingest_alone(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    async def _neo4j_alive(_pid, *, db=None):
        return True

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)
    monkeypatch.setattr(tasks, "is_neo4j_graph_indexing_alive", _neo4j_alive)

    project = Project(
        id=pid,
        name="Active Neo4j",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "neo4j"},
        graph_index_status=GraphIndexState(
            backend="neo4j",
            status="indexing",
            indexing_started_at=datetime.now(timezone.utc),
        ).to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    assert await tasks.reconcile_stale_graph_index(pid) is False
    await db_session.refresh(project)
    assert GraphIndexState.from_db(project.graph_index_status).status == "indexing"


@pytest.mark.asyncio
async def test_reconcile_does_not_overwrite_ready_committed_during_inspection(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow task inspection must not replace a newer worker result."""
    from datetime import datetime, timezone

    pid = uuid4()

    def _session_maker():
        class _Ctx:
            async def __aenter__(self_inner):
                return db_session

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()

    project = Project(
        id=pid,
        name="Completes During Inspect",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={"graph_backend": "neo4j"},
        graph_index_status=GraphIndexState(
            backend="neo4j",
            status="indexing",
            indexing_started_at=datetime.now(timezone.utc),
        ).to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    async def _finishes_while_inspecting(_pid, _backend, *, db=None):
        project.graph_index_status = GraphIndexState(
            backend="neo4j",
            status="ready",
            entity_count=3,
            passage_count=1,
        ).to_db()
        await db_session.commit()
        return False

    monkeypatch.setattr(tasks, "async_session_maker", _session_maker)
    monkeypatch.setattr(tasks, "_graph_indexing_alive", _finishes_while_inspecting)

    assert await tasks.reconcile_stale_graph_index(pid) is False
    await db_session.refresh(project)
    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "ready"
    assert state.backend == "neo4j"


@pytest.mark.asyncio
async def test_neo4j_status_update_preserves_backend_discriminator(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import document_worker

    project = Project(
        id=uuid4(),
        name="Neo4j Status",
        owner_id=uuid4(),
        rag_mode=RagMode.GRAPH,
        rag_config={},
        graph_index_status=GraphIndexState(backend="neo4j", status="indexing").to_db(),
    )
    db_session.add(project)
    await db_session.commit()

    store = MagicMock()
    store.get_stats.return_value = MagicMock(entity_count=2, passage_count=1)
    monkeypatch.setattr(document_worker, "get_neo4j_store", lambda: store)

    await document_worker._update_graph_index_status(
        db_session, project, status="ready"
    )

    state = GraphIndexState.from_db(project.graph_index_status)
    assert state.status == "ready"
    assert state.backend == "neo4j"


def test_is_graph_rebuild_alive_false_on_terminal_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = uuid4()

    existing = MagicMock()
    existing.state = "FAILURE"

    mock_task = MagicMock()
    mock_task.app = MagicMock()

    with (
        patch("app.services.celery_tasks.rebuild_graph_index_task", mock_task),
        patch("app.services.graph_index_tasks.AsyncResult", return_value=existing),
    ):
        assert tasks.is_graph_rebuild_alive(pid) is False


def test_is_graph_rebuild_alive_detects_orphaned_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STARTED in result backend but no worker owns the task → dead."""
    pid = uuid4()

    existing = MagicMock()
    existing.state = "STARTED"

    insp = MagicMock()
    insp.active.return_value = {"worker@host": []}
    insp.reserved.return_value = {"worker@host": []}
    insp.scheduled.return_value = {"worker@host": []}

    mock_app = MagicMock()
    mock_app.control.inspect.return_value = insp
    mock_task = MagicMock()
    mock_task.app = mock_app

    with (
        patch("app.services.celery_tasks.rebuild_graph_index_task", mock_task),
        patch("app.services.graph_index_tasks.AsyncResult", return_value=existing),
    ):
        assert tasks.is_graph_rebuild_alive(pid) is False


def test_schedule_graph_rebuild_replaces_orphaned_received() -> None:
    pid = uuid4()
    existing = MagicMock()
    existing.state = "RECEIVED"

    mock_task = MagicMock()
    mock_task.app = MagicMock()
    mock_task.apply_async.return_value.id = "replacement"

    with (
        patch("app.services.celery_tasks.rebuild_graph_index_task", mock_task),
        patch("app.services.celery_schedule.AsyncResult", return_value=existing),
    ):
        result = tasks.schedule_graph_index_rebuild(pid)

    assert result == "replacement"
    mock_task.apply_async.assert_called_once()


def test_schedule_graph_rebuild_replaces_orphaned_started() -> None:
    pid = uuid4()
    existing = MagicMock()
    existing.state = "STARTED"

    mock_task = MagicMock()
    mock_task.app = MagicMock()
    mock_task.apply_async.return_value.id = "replacement"

    with (
        patch("app.services.celery_tasks.rebuild_graph_index_task", mock_task),
        patch("app.services.celery_schedule.AsyncResult", return_value=existing),
    ):
        result = tasks.schedule_graph_index_rebuild(pid)

    assert result == "replacement"
    mock_task.apply_async.assert_called_once()


def test_schedule_graph_rebuild_replaces_pending() -> None:
    """Unknown PENDING (first schedule) must enqueue without revoke."""
    pid = uuid4()
    task_id = tasks.graph_rebuild_task_id(pid)

    existing = MagicMock()
    existing.state = "PENDING"

    async_result = MagicMock()
    async_result.id = task_id

    mock_task = MagicMock()
    mock_task.app = MagicMock()
    mock_task.apply_async.return_value = async_result

    with (
        patch("app.services.celery_tasks.rebuild_graph_index_task", mock_task),
        patch("app.services.celery_schedule.AsyncResult", return_value=existing),
        patch(
            "app.services.celery_schedule.celery_task_known_to_workers",
            return_value=False,
        ),
    ):
        result = tasks.schedule_graph_index_rebuild(pid, debounce_seconds=1.0)

    assert result == task_id
    mock_task.app.control.revoke.assert_not_called()
    mock_task.apply_async.assert_called_once()
    kwargs = mock_task.apply_async.call_args.kwargs
    assert kwargs["task_id"] == task_id
    assert kwargs["queue"] == "graph"
    assert kwargs["countdown"] == 1.0
    assert kwargs["kwargs"]["force_full_rebuild"] is False
    assert kwargs["kwargs"]["compact_cache"] is False


def test_schedule_graph_rebuild_forwards_full_cache_compaction() -> None:
    pid = uuid4()
    mock_task = MagicMock()
    mock_task.app = MagicMock()
    mock_task.apply_async.return_value.id = "full-rebuild"

    with (
        patch("app.services.celery_tasks.rebuild_graph_index_task", mock_task),
        patch(
            "app.services.celery_schedule.prepare_reusable_task_id",
            return_value="full-rebuild",
        ),
    ):
        result = tasks.schedule_graph_index_rebuild(
            pid,
            force_full_rebuild=True,
            compact_cache=True,
        )

    assert result == "full-rebuild"
    kwargs = mock_task.apply_async.call_args.kwargs["kwargs"]
    assert kwargs["force_full_rebuild"] is True
    assert kwargs["compact_cache"] is True


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
async def test_graph_extractor_failfast_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
