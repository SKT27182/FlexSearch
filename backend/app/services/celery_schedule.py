"""Safe Celery enqueue helpers.

Never ``revoke()`` a task id and then ``apply_async(..., task_id=same_id)``.
Celery workers keep a revoked set: the re-enqueue is discarded immediately
(seen as ``Discarding revoked task``), leaving documents stuck at progress.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from celery.result import AsyncResult

from app.utils.logger import create_logger

logger = create_logger(__name__)

RUNNING = frozenset({"STARTED", "RETRY", "RECEIVED"})
TERMINAL = frozenset({"SUCCESS", "FAILURE", "REVOKED"})


def celery_task_known_to_workers(task_id: str, app: Any) -> bool:
    """True if any worker lists this task as active, reserved, or scheduled."""
    try:
        inspector = app.control.inspect(timeout=1.0)
        if inspector is None:
            return False
        for getter in (inspector.active, inspector.reserved, inspector.scheduled):
            mapping = getter() or {}
            for worker_items in mapping.values():
                for item in worker_items or []:
                    if not isinstance(item, dict):
                        continue
                    if item.get("id") == task_id:
                        return True
                    req = item.get("request")
                    if isinstance(req, dict) and req.get("id") == task_id:
                        return True
    except Exception:
        logger.debug("Celery inspect failed for task_id=%s", task_id, exc_info=True)
    return False


def prepare_reusable_task_id(
    task_id: str,
    app: Any,
    *,
    replace_queued: bool = False,
) -> str | None:
    """
    Decide which task_id to use for apply_async.

    Returns:
      - ``None`` if an in-flight/queued task should be left alone (coalesce)
      - ``task_id`` (possibly new) to pass to apply_async

    Never revokes then reuses the same id.
    """
    existing = AsyncResult(task_id, app=app)
    try:
        state = existing.state
    except Exception:
        # Broker/result backend briefly unreachable — treat as unknown.
        logger.warning(
            "Could not read Celery state for %s; enqueueing fresh",
            task_id,
            exc_info=True,
        )
        return task_id

    if state in RUNNING:
        # STARTED/RECEIVED in Redis can be a ghost after worker crash/restart.
        # Only coalesce when a live worker still lists the task.
        if celery_task_known_to_workers(task_id, app):
            return None
        logger.warning(
            "Stale Celery state=%s for %s (not on any worker); re-enqueueing",
            state,
            task_id,
        )
        try:
            existing.forget()
        except Exception:
            pass
        return f"{task_id}:{uuid4().hex[:8]}"

    known = celery_task_known_to_workers(task_id, app)
    if state == "PENDING" and known:
        if not replace_queued:
            return None
        # Debounce/replace: revoke the queued message, then MUST use a new id.
        try:
            app.control.revoke(task_id, terminate=False)
            existing.forget()
        except Exception:
            logger.debug("Could not revoke queued task %s", task_id, exc_info=True)
        return f"{task_id}:{uuid4().hex[:8]}"

    if state in TERMINAL:
        try:
            existing.forget()
        except Exception:
            pass
        # REVOKED ids stay blacklisted on workers — never reuse them.
        if state == "REVOKED":
            return f"{task_id}:{uuid4().hex[:8]}"

    # Unknown PENDING (never scheduled) or cleared SUCCESS/FAILURE — safe to reuse id.
    # Do NOT revoke: that blacklists the id and the next apply_async is discarded.
    return task_id


def prepare_replace_task_id(task_id: str, app: Any) -> str:
    """
    Force-replace any prior task (including RUNNING).

    Revokes the old id when needed, then returns a fresh id so the new
    enqueue is not discarded as revoked.
    """
    existing = AsyncResult(task_id, app=app)
    try:
        state = existing.state
    except Exception:
        logger.warning(
            "Could not read Celery state for %s; enqueueing with base id",
            task_id,
            exc_info=True,
        )
        return task_id

    if state in RUNNING or (
        state == "PENDING" and celery_task_known_to_workers(task_id, app)
    ):
        try:
            app.control.revoke(task_id, terminate=state in RUNNING)
            existing.forget()
        except Exception:
            logger.debug("Could not revoke task %s for replace", task_id, exc_info=True)
        return f"{task_id}:{uuid4().hex[:8]}"

    if state in TERMINAL:
        try:
            existing.forget()
        except Exception:
            pass
        if state == "REVOKED":
            return f"{task_id}:{uuid4().hex[:8]}"
    return task_id
