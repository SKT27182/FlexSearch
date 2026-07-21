"""Rate-limit aware retry for Microsoft GraphRAG LLM calls.

GraphRAG's built-in exponential backoff retries internally without logging.
When NVIDIA returns 429 with a Retry-After hint, we sleep for that duration,
log each retry clearly, and resume instead of failing the whole index build.
"""

from __future__ import annotations

import asyncio
import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__)

_INSTALLED = False
_RATE_LIMIT_MARKERS = ("429", "rate limit", "too many requests", "ratelimit")


def is_rate_limit_error(exc: BaseException) -> bool:
    if exc.__class__.__name__ == "RateLimitError":
        return True
    status = getattr(exc, "status_code", None)
    if status == 429:
        return True
    msg = str(exc).lower()
    return any(marker in msg for marker in _RATE_LIMIT_MARKERS)


def describe_llm_error(exc: BaseException) -> str:
    """Short, log-friendly summary of an LLM provider error."""
    if is_rate_limit_error(exc):
        return "HTTP 429 Too Many Requests"
    status = getattr(exc, "status_code", None)
    if status is not None:
        return f"HTTP {status} {exc.__class__.__name__}"
    name = exc.__class__.__name__
    msg = str(exc).strip().replace("\n", " ")
    if len(msg) > 200:
        msg = msg[:197] + "..."
    return f"{name}: {msg}" if msg else name


def rate_limit_wait_seconds(exc: BaseException) -> float:
    """Honor Retry-After (or similar) from the provider response when present."""
    cap = settings.graphrag_rate_limit_max_wait_seconds

    response = getattr(exc, "response", None)
    if response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            for key in ("retry-after", "Retry-After", "x-ratelimit-reset-after"):
                raw = headers.get(key)
                if raw is None:
                    continue
                try:
                    wait = float(raw)
                except (TypeError, ValueError):
                    continue
                if wait > 0:
                    return min(wait, cap)

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        for key in ("retry_after", "retryAfter", "wait_seconds", "wait"):
            raw = body.get(key)
            if raw is None:
                continue
            try:
                wait = float(raw)
            except (TypeError, ValueError):
                continue
            if wait > 0:
                return min(wait, cap)

    match = re.search(
        r"retry(?:\s+after)?\s+(\d+(?:\.\d+)?)\s*(?:s|sec|seconds)?",
        str(exc),
        re.IGNORECASE,
    )
    if match:
        return min(float(match.group(1)), cap)

    return settings.graphrag_rate_limit_default_wait_seconds


def _log_llm_retry(
    exc: BaseException,
    *,
    attempt: int,
    max_attempts: int,
    wait_seconds: float,
) -> None:
    detail = describe_llm_error(exc)
    if is_rate_limit_error(exc):
        logger.warning(
            "GraphRAG LLM %s; waiting %.1fs before retry %d/%d",
            detail,
            wait_seconds,
            attempt,
            max_attempts,
        )
    else:
        logger.warning(
            "GraphRAG LLM call failed (%s); retrying in %.1fs (%d/%d)",
            detail,
            wait_seconds,
            attempt,
            max_attempts,
        )


async def retry_on_rate_limit_async(
    coro_factory: Any,
    *,
    context: str = "GraphRAG LLM",
) -> Any:
    """Run an async callable, sleeping through 429s until success or max attempts."""
    max_attempts = settings.graphrag_rate_limit_max_retries
    for attempt in range(1, max_attempts + 1):
        try:
            result = coro_factory()
            if asyncio.iscoroutine(result):
                return await result
            return result
        except Exception as exc:
            if not is_rate_limit_error(exc) or attempt >= max_attempts:
                raise
            wait = rate_limit_wait_seconds(exc)
            logger.warning(
                "%s %s; waiting %.1fs before retry %d/%d",
                context,
                describe_llm_error(exc),
                wait,
                attempt,
                max_attempts,
            )
            await asyncio.sleep(wait)
    raise RuntimeError(f"{context}: rate limit retries exhausted")


def install_graphrag_rate_limit_retry() -> None:
    """Patch GraphRAG's exponential retry to log and honor provider Retry-After."""
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_exponential_retry()
    _INSTALLED = True


def _patch_exponential_retry() -> None:
    from graphrag_llm.retry.exponential_retry import ExponentialRetry

    if getattr(ExponentialRetry, "_flexsearch_rate_limit", False):
        return

    def retry_with_logging(
        self, *, func: Callable[..., Any], input_args: dict[str, Any]
    ) -> Any:
        retries = 0
        delay = 1.0
        metrics = input_args.get("metrics")
        while True:
            try:
                return func(**input_args)
            except Exception as exc:
                if exc.__class__.__name__ in self._exceptions_to_skip:
                    raise
                if retries >= self._max_retries:
                    logger.error(
                        "GraphRAG LLM call failed after %d retries: %s",
                        retries,
                        describe_llm_error(exc),
                    )
                    raise
                retries += 1
                if is_rate_limit_error(exc):
                    sleep_delay = rate_limit_wait_seconds(exc)
                else:
                    delay *= self._base_delay
                    sleep_delay = min(
                        self._max_delay,
                        delay + (self._jitter * random.uniform(0, 1)),  # noqa: S311
                    )
                _log_llm_retry(
                    exc,
                    attempt=retries,
                    max_attempts=self._max_retries,
                    wait_seconds=sleep_delay,
                )
                time.sleep(sleep_delay)
            finally:
                if metrics is not None:
                    metrics["retries"] = retries
                    metrics["requests_with_retries"] = 1 if retries > 0 else 0

    async def retry_async_with_logging(
        self,
        *,
        func: Callable[..., Awaitable[Any]],
        input_args: dict[str, Any],
    ) -> Any:
        retries = 0
        delay = 1.0
        metrics = input_args.get("metrics")
        while True:
            try:
                return await func(**input_args)
            except Exception as exc:
                if exc.__class__.__name__ in self._exceptions_to_skip:
                    raise
                if retries >= self._max_retries:
                    logger.error(
                        "GraphRAG LLM call failed after %d retries: %s",
                        retries,
                        describe_llm_error(exc),
                    )
                    raise
                retries += 1
                if is_rate_limit_error(exc):
                    sleep_delay = rate_limit_wait_seconds(exc)
                else:
                    delay *= self._base_delay
                    sleep_delay = min(
                        self._max_delay,
                        delay + (self._jitter * random.uniform(0, 1)),  # noqa: S311
                    )
                _log_llm_retry(
                    exc,
                    attempt=retries,
                    max_attempts=self._max_retries,
                    wait_seconds=sleep_delay,
                )
                await asyncio.sleep(sleep_delay)
            finally:
                if metrics is not None:
                    metrics["retries"] = retries
                    metrics["requests_with_retries"] = 1 if retries > 0 else 0

    ExponentialRetry.retry = retry_with_logging  # type: ignore[method-assign]
    ExponentialRetry.retry_async = retry_async_with_logging  # type: ignore[method-assign]
    ExponentialRetry._flexsearch_rate_limit = True
