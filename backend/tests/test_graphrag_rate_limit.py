"""Tests for GraphRAG 429 / Retry-After handling."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from openai import RateLimitError

from app.services import graphrag_rate_limit as rl


def test_rate_limit_wait_seconds_from_retry_after_header() -> None:
    response = httpx.Response(
        429,
        headers={"retry-after": "45"},
        request=httpx.Request("POST", "https://example.com/v1/chat/completions"),
    )
    exc = RateLimitError("rate limited", response=response, body={"status": 429})
    assert rl.rate_limit_wait_seconds(exc) == 45.0


def test_rate_limit_wait_seconds_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setattr(rl.settings, "graphrag_rate_limit_default_wait_seconds", 90.0)

    class TooManyRequests(Exception):
        status_code = 429

    assert rl.rate_limit_wait_seconds(TooManyRequests("429 Too Many Requests")) == 90.0


def test_is_rate_limit_error_detects_status_code() -> None:
    class Fake429(Exception):
        status_code = 429

    assert rl.is_rate_limit_error(Fake429("x"))


@pytest.mark.asyncio
async def test_retry_on_rate_limit_async_waits_and_retries(monkeypatch) -> None:
    monkeypatch.setattr(rl.settings, "graphrag_rate_limit_max_retries", 3)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RateLimitError(
                "429",
                response=httpx.Response(
                    429,
                    headers={"retry-after": "0.01"},
                    request=httpx.Request("POST", "https://example.com"),
                ),
                body={"status": 429},
            )
        return "ok"

    result = await rl.retry_on_rate_limit_async(factory, context="test")
    assert result == "ok"
    assert calls["n"] == 3


def test_describe_llm_error_rate_limit() -> None:
    exc = RateLimitError(
        "429",
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "https://example.com"),
        ),
        body={"status": 429},
    )
    assert rl.describe_llm_error(exc) == "HTTP 429 Too Many Requests"


def test_patch_exponential_retry_rate_limit_logs_and_retries(
    monkeypatch, caplog
) -> None:
    import logging

    from graphrag_llm.retry.exponential_retry import ExponentialRetry

    ExponentialRetry._flexsearch_rate_limit = False  # type: ignore[attr-defined]
    rl._INSTALLED = False
    monkeypatch.setattr(rl.settings, "graphrag_rate_limit_default_wait_seconds", 0.01)

    rl.install_graphrag_rate_limit_retry()

    attempts = {"n": 0}

    async def failing_func(**_kwargs):
        attempts["n"] += 1
        raise RateLimitError(
            "429",
            response=httpx.Response(
                429,
                headers={"retry-after": "0.01"},
                request=httpx.Request("POST", "https://example.com"),
            ),
            body={"status": 429},
        )

    retrier = ExponentialRetry(max_retries=1)

    async def run():
        await retrier.retry_async(func=failing_func, input_args={})

    with caplog.at_level(logging.WARNING, logger="app.services.graphrag_rate_limit"):
        with pytest.raises(RateLimitError):
            asyncio.run(run())

    assert attempts["n"] >= 2
    assert any(
        "HTTP 429 Too Many Requests" in record.message for record in caplog.records
    )
    assert any("waiting" in record.message for record in caplog.records)


def test_patch_graphrag_runtime_settings_injects_concurrency(monkeypatch) -> None:
    from app.services.graphrag_workspace import _patch_graphrag_runtime_settings

    monkeypatch.setattr(
        "app.services.graphrag_workspace.settings.graphrag_concurrent_requests", 6
    )
    raw = (
        "completion_models:\n"
        "  default_completion_model:\n"
        "    retry:\n"
        "      type: exponential_backoff\n"
    )
    patched = _patch_graphrag_runtime_settings(raw)
    assert "concurrent_requests: 6" in patched
    assert "max_retries: 12" in patched
