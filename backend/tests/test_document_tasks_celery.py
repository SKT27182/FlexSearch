"""Celery ingest scheduling — unknown task ids must still enqueue."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.services.document_tasks import schedule_process_document
from app.services.document_worker import DocumentIngestError, ReindexMode


def test_run_async_closes_loop_bound_clients() -> None:
    """Celery must not carry async clients into its next asyncio.run loop."""
    from app.services.celery_tasks import _run_async

    close_redis = AsyncMock()
    engine = MagicMock()
    engine.dispose = AsyncMock()

    async def work() -> str:
        return "ok"

    with (
        patch("app.services.redis_client.close_redis", close_redis),
        patch("app.db.postgres.engine", engine),
    ):
        assert _run_async(work()) == "ok"

    close_redis.assert_awaited_once_with()
    engine.dispose.assert_awaited_once_with()


def test_schedule_enqueues_when_async_result_pending_unknown() -> None:
    """Celery AsyncResult.state is PENDING for never-seen ids — must apply_async.

    Must NOT revoke first: revoke blacklists the id and the worker discards
    the re-enqueue (documents stuck at 25% / Saved to storage).
    """
    document_id = uuid4()
    project_id = uuid4()
    task_id = f"ingest:{document_id}:auto"

    existing = MagicMock()
    existing.state = "PENDING"

    async_result = MagicMock()
    async_result.id = task_id

    mock_task = MagicMock()
    mock_task.app = MagicMock()
    mock_task.apply_async.return_value = async_result

    with (
        patch(
            "app.services.celery_tasks.process_document_task",
            mock_task,
        ),
        patch(
            "app.services.celery_schedule.AsyncResult",
            return_value=existing,
        ),
        patch(
            "app.services.celery_schedule.celery_task_known_to_workers",
            return_value=False,
        ),
    ):
        result = schedule_process_document(document_id, project_id)

    assert result == task_id
    mock_task.app.control.revoke.assert_not_called()
    mock_task.apply_async.assert_called_once()
    kwargs = mock_task.apply_async.call_args.kwargs
    assert kwargs["task_id"] == task_id
    assert kwargs["queue"] == "ingest"


def test_schedule_coalesces_when_already_started() -> None:
    document_id = uuid4()
    project_id = uuid4()
    task_id = f"ingest:{document_id}:auto"

    existing = MagicMock()
    existing.state = "STARTED"

    mock_task = MagicMock()
    mock_task.app = MagicMock()

    with (
        patch(
            "app.services.celery_tasks.process_document_task",
            mock_task,
        ),
        patch(
            "app.services.celery_schedule.AsyncResult",
            return_value=existing,
        ),
        patch(
            "app.services.celery_schedule.celery_task_known_to_workers",
            return_value=True,
        ),
    ):
        result = schedule_process_document(
            document_id, project_id, mode=ReindexMode.AUTO
        )

    assert result == task_id
    mock_task.apply_async.assert_not_called()


def test_schedule_recovers_stale_started_not_on_worker() -> None:
    """Ghost STARTED after worker crash must re-enqueue with a fresh task id."""
    document_id = uuid4()
    project_id = uuid4()
    base_id = f"ingest:{document_id}:full"

    existing = MagicMock()
    existing.state = "STARTED"

    async_result = MagicMock()
    async_result.id = f"{base_id}:deadbeef"

    mock_task = MagicMock()
    mock_task.app = MagicMock()
    mock_task.apply_async.return_value = async_result

    with (
        patch(
            "app.services.celery_tasks.process_document_task",
            mock_task,
        ),
        patch(
            "app.services.celery_schedule.AsyncResult",
            return_value=existing,
        ),
        patch(
            "app.services.celery_schedule.celery_task_known_to_workers",
            return_value=False,
        ),
    ):
        result = schedule_process_document(
            document_id,
            project_id,
            force_full_extract=True,
            mode=ReindexMode.FULL,
        )

    assert result == async_result.id
    existing.forget.assert_called()
    mock_task.apply_async.assert_called_once()
    assert mock_task.apply_async.call_args.kwargs["task_id"].startswith(base_id)
    assert mock_task.apply_async.call_args.kwargs["task_id"] != base_id


def test_summary_schedule_replaces_when_already_started() -> None:
    """Reindex must enqueue a fresh summary after wipe (new id after revoke)."""
    from app.services.summary_tasks import schedule_document_summary

    document_id = uuid4()
    project_id = uuid4()
    base_id = f"summary:{document_id}"

    existing = MagicMock()
    existing.state = "STARTED"

    async_result = MagicMock()
    async_result.id = f"{base_id}:abc12345"

    mock_task = MagicMock()
    mock_task.app = MagicMock()
    mock_task.apply_async.return_value = async_result

    with (
        patch(
            "app.services.celery_tasks.build_document_summaries_task",
            mock_task,
        ),
        patch(
            "app.services.celery_schedule.AsyncResult",
            return_value=existing,
        ),
        patch(
            "app.services.celery_schedule.celery_task_known_to_workers",
            return_value=True,
        ),
    ):
        result = schedule_document_summary(document_id, project_id, 3)

    assert result == async_result.id
    mock_task.app.control.revoke.assert_called_once_with(base_id, terminate=True)
    mock_task.apply_async.assert_called_once()
    kwargs = mock_task.apply_async.call_args.kwargs
    assert kwargs["task_id"].startswith(f"{base_id}:")
    assert kwargs["task_id"] != base_id
    assert kwargs["queue"] == "summary"
    assert kwargs["args"] == [str(document_id), str(project_id), 3]


def test_cancel_document_ingest_revokes_all_modes() -> None:
    """Delete must terminate in-flight graph ingest before Neo4j wipe."""
    from app.services.document_tasks import cancel_document_ingest

    document_id = uuid4()
    mock_task = MagicMock()
    mock_task.app = MagicMock()

    with (
        patch(
            "app.services.celery_tasks.process_document_task",
            mock_task,
        ),
        patch("app.services.document_tasks.AsyncResult") as mock_ar,
    ):
        cancel_document_ingest(document_id)

    expected_ids = {f"ingest:{document_id}:{mode.value}" for mode in ReindexMode}
    revoked = {call.args[0] for call in mock_task.app.control.revoke.call_args_list}
    assert revoked == expected_ids
    for call in mock_task.app.control.revoke.call_args_list:
        assert call.kwargs.get("terminate") is True
    assert mock_ar.call_count == len(ReindexMode)


def test_process_document_task_raises_when_ingest_fails() -> None:
    """Celery must not report SUCCESS when process_document fails internally."""
    from app.services.celery_tasks import process_document_task

    document_id = str(uuid4())
    project_id = str(uuid4())

    def _fail(coro):
        coro.close()
        raise DocumentIngestError("No text could be extracted from this file")

    with patch("app.services.celery_tasks._run_async", side_effect=_fail):
        with pytest.raises(DocumentIngestError, match="No text"):
            process_document_task.run(
                document_id,
                project_id,
                force_full_extract=False,
                mode="auto",
            )


def test_process_document_task_returns_ok_on_success() -> None:
    from app.services.celery_tasks import process_document_task

    document_id = str(uuid4())
    project_id = str(uuid4())

    def _ok(coro):
        coro.close()
        return None

    with patch("app.services.celery_tasks._run_async", side_effect=_ok):
        result = process_document_task.run(
            document_id,
            project_id,
            force_full_extract=False,
            mode="auto",
        )

    assert result == {"document_id": document_id, "status": "ok"}
