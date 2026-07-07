"""Tests for GraphRAG isolated event loop runner."""

from __future__ import annotations

import asyncio

import pytest

from app.services.graphrag_runner import (
    _run_with_stdlib_loop,
    _stdlib_event_loop,
    run_in_std_event_loop,
    run_sync_in_std_thread,
)


@pytest.mark.asyncio
async def test_run_in_std_event_loop_executes_coroutine() -> None:
    async def _work() -> str:
        await asyncio.sleep(0)
        return "ok"

    result = await run_in_std_event_loop(_work)
    assert result == "ok"


@pytest.mark.asyncio
async def test_stdlib_event_loop_is_not_uvloop_when_uvloop_installed() -> None:
    uvloop = pytest.importorskip("uvloop")
    policy = asyncio.get_event_loop_policy()
    uvloop.install()
    try:
        loop = _stdlib_event_loop()
        assert type(loop).__name__ != "Loop"  # uvloop.Loop is named Loop
        loop.close()
    finally:
        asyncio.set_event_loop_policy(policy)


@pytest.mark.asyncio
async def test_run_sync_in_std_thread_executes_sync_callable() -> None:
    def _work() -> str:
        return "sync-ok"

    result = await run_sync_in_std_thread(_work)
    assert result == "sync-ok"


@pytest.mark.asyncio
async def test_run_sync_in_std_thread_imports_graphrag_under_uvloop() -> None:
    uvloop = pytest.importorskip("uvloop")
    policy = asyncio.get_event_loop_policy()
    uvloop.install()
    try:

        def _import_graphrag() -> str:
            from graphrag.cli.initialize import initialize_project_at  # noqa: F401

            loop = asyncio.get_event_loop()
            assert type(loop).__name__ != "Loop"
            return "imported"

        assert await run_sync_in_std_thread(_import_graphrag) == "imported"
    finally:
        asyncio.set_event_loop_policy(policy)


@pytest.mark.asyncio
async def test_run_with_stdlib_loop_runs_coroutine_fn() -> None:
    async def _work() -> str:
        await asyncio.sleep(0)
        return "coro-ok"

    assert _run_with_stdlib_loop(_work) == "coro-ok"
