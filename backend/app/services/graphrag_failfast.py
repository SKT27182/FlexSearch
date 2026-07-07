"""Make Microsoft GraphRAG indexing fail fast with visible errors.

GraphRAG's ``GraphExtractor`` swallows per-chunk LLM failures and returns empty
entity dataframes, so 234 auth errors look like "No entities detected". The
parallel ``derive_from_rows`` helper also collects errors and only raises after
every chunk finishes. These patches re-raise on the first failure and cancel
sibling tasks so the build stops immediately with the real exception.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Coroutine, Hashable
from typing import Any, TypeVar, cast

from app.services.graphrag_rate_limit import is_rate_limit_error, retry_on_rate_limit_async
from app.utils.logger import create_logger

logger = create_logger(__name__)

_INSTALLED = False

ItemType = TypeVar("ItemType")


def install_graphrag_failfast() -> None:
    """Install fail-fast patches once per process (idempotent)."""
    global _INSTALLED
    if _INSTALLED:
        return
    _install_failfast_graph_extractor()
    _install_failfast_derive_from_rows()
    _INSTALLED = True


def _install_failfast_graph_extractor() -> None:
    from graphrag.index.operations.extract_graph.graph_extractor import (
        GraphExtractor,
        RECORD_DELIMITER,
        TUPLE_DELIMITER,
    )

    if getattr(GraphExtractor, "_flexsearch_failfast", False):
        return

    async def failfast_call(
        self: GraphExtractor,
        text: str,
        entity_types: list[str],
        source_id: str,
    ):
        try:
            result = await retry_on_rate_limit_async(
                lambda: self._process_document(text, entity_types),
                context=f"GraphRAG extract_graph source_id={source_id}",
            )
        except Exception as exc:
            if is_rate_limit_error(exc):
                logger.error(
                    "GraphRAG entity extraction hit rate limit for source_id=%s after retries: %s",
                    source_id,
                    exc,
                )
            else:
                logger.exception(
                    "GraphRAG entity extraction failed for source_id=%s: %s",
                    source_id,
                    exc,
                )
            raise
        return self._process_result(
            result,
            source_id,
            TUPLE_DELIMITER,
            RECORD_DELIMITER,
        )

    GraphExtractor.__call__ = failfast_call  # type: ignore[method-assign]
    GraphExtractor._flexsearch_failfast = True


def _install_failfast_derive_from_rows() -> None:
    import graphrag.index.utils.derive_from_rows as dfr

    if getattr(dfr, "_flexsearch_failfast", False):
        return

    async def _derive_from_rows_base_failfast(
        input,
        transform: Callable[..., Awaitable[ItemType]],
        callbacks,
        gather,
        progress_msg: str = "",
    ) -> list[ItemType | None]:
        from graphrag.logger.progress import progress_ticker

        tick = progress_ticker(
            callbacks.progress, num_total=len(input), description=progress_msg
        )

        async def execute(row: tuple[Any, Any]) -> ItemType:
            try:
                result = transform(row[1])
                if inspect.iscoroutine(result):
                    result = await result
                return cast("ItemType", result)
            finally:
                tick(1)

        try:
            return await gather(execute)
        finally:
            tick.done()

    async def derive_from_rows_asyncio_failfast(
        input,
        transform: Callable[..., Awaitable[ItemType]],
        callbacks,
        num_threads: int = 4,
        progress_msg: str = "",
    ) -> list[ItemType | None]:
        semaphore = asyncio.Semaphore(num_threads or 4)

        async def gather(execute) -> list[ItemType | None]:
            async def execute_row_protected(
                row: tuple[Hashable, Any],
            ) -> ItemType:
                async with semaphore:
                    return await execute(row)

            tasks = [
                asyncio.create_task(execute_row_protected(row))
                for row in input.iterrows()
            ]
            return await _gather_failfast(tasks)

        return await _derive_from_rows_base_failfast(
            input, transform, callbacks, gather, progress_msg
        )

    async def derive_from_rows_asyncio_threads_failfast(
        input,
        transform: Callable[..., Awaitable[ItemType]],
        callbacks,
        num_threads: int | None = 4,
        progress_msg: str = "",
    ) -> list[ItemType | None]:
        semaphore = asyncio.Semaphore(num_threads or 4)

        async def gather(execute) -> list[ItemType | None]:
            tasks = [asyncio.to_thread(execute, row) for row in input.iterrows()]

            async def execute_task(task: Coroutine) -> ItemType:
                async with semaphore:
                    thread = await task
                    return await thread

            wrapped = [asyncio.create_task(execute_task(task)) for task in tasks]
            return await _gather_failfast(wrapped)

        return await _derive_from_rows_base_failfast(
            input, transform, callbacks, gather, progress_msg
        )

    dfr._derive_from_rows_base = _derive_from_rows_base_failfast
    dfr.derive_from_rows_asyncio = derive_from_rows_asyncio_failfast
    dfr.derive_from_rows_asyncio_threads = derive_from_rows_asyncio_threads_failfast
    dfr._flexsearch_failfast = True


async def _gather_failfast(tasks: list[asyncio.Task]) -> list[Any]:
    """``asyncio.gather`` that cancels siblings when any task raises."""
    if not tasks:
        return []
    try:
        return await asyncio.gather(*tasks)
    except Exception:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
