"""Run Microsoft GraphRAG on a stdlib asyncio loop (uvloop is incompatible with nest_asyncio)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")

# GraphRAG indexing is CPU/IO heavy; keep concurrency low.
_graphrag_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="graphrag")


def _stdlib_event_loop() -> asyncio.AbstractEventLoop:
    """Create a real stdlib loop even when uvicorn installed uvloop globally."""
    return asyncio.SelectorEventLoop()


def _run_with_stdlib_loop(fn: Callable[[], T]) -> T:
    """Run sync or async GraphRAG work on a fresh stdlib loop in a worker thread."""
    loop = _stdlib_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            return loop.run_until_complete(result)
        return result
    finally:
        asyncio.set_event_loop(None)
        loop.close()


async def run_in_std_event_loop(
    coro_fn: Callable[[], Coroutine[None, None, T]],
) -> T:
    """Execute async GraphRAG work off the uvicorn uvloop in a fresh stdlib loop."""

    def _worker() -> T:
        return _run_with_stdlib_loop(coro_fn)

    return await asyncio.get_running_loop().run_in_executor(_graphrag_executor, _worker)


async def run_sync_in_std_thread(fn: Callable[[], T]) -> T:
    """Execute sync GraphRAG imports/calls off the uvicorn uvloop thread."""

    return await asyncio.get_running_loop().run_in_executor(
        _graphrag_executor, lambda: _run_with_stdlib_loop(fn)
    )
