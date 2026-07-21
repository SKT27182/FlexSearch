"""Phase 5: observability, SSRF, rate limits, job ACL, eval."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.core.rate_limit import _memory
from app.eval.harness import run_eval
from app.eval.metrics import faithfulness_score, retrieval_at_k
from app.observability.metrics import MetricsRegistry
from app.services.job_events import (
    get_job_meta,
    parse_project_id_from_job_id,
    register_job_meta,
)
from app.services.url_safety import (
    UnsafeURLError,
    assert_public_url,
    is_safe_public_url,
)


async def create_user_and_login(client: AsyncClient, email: str) -> str:
    await client.post(
        "/api/auth/register",
        json={"email": email, "name": "Test User", "password": "password123"},
    )
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "password123"},
    )
    return response.json()["access_token"]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_metrics_empty_retrieval_rate():
    m = MetricsRegistry()
    m.record_chat(path="query", empty_retrieval=False, latency_ms=10)
    m.record_chat(path="query", empty_retrieval=True, latency_ms=20)
    assert m.empty_retrieval_rate() == 0.5
    text = m.render_prometheus()
    assert "flexsearch_chat_requests_total" in text
    assert "flexsearch_chat_empty_retrieval_total" in text


def test_histogram_prometheus_cumulative_buckets():
    """+Inf bucket must equal _count (Prometheus histogram contract)."""
    m = MetricsRegistry()
    m.observe_stage("retrieve", 0.01, strategy="dense")
    m.observe_stage("retrieve", 0.2, strategy="dense")
    text = m.render_prometheus()
    buckets: dict[str, float] = {}
    count = None
    for line in text.splitlines():
        if 'stage="retrieve"' not in line or 'strategy="dense"' not in line:
            continue
        if "_bucket{" in line and 'le="' in line:
            le = line.split('le="')[1].split('"')[0]
            buckets[le] = float(line.rsplit(" ", 1)[-1])
        elif line.startswith("flexsearch_stage_latency_seconds_count{"):
            count = float(line.rsplit(" ", 1)[-1])
    assert count == 2.0
    assert buckets.get("+Inf") == count
    assert buckets.get("0.005") == 0.0
    assert buckets.get("0.01") == 1.0
    assert buckets.get("0.25") == 2.0
    # Monotonic non-decreasing
    ordered = [
        buckets[le]
        for le in sorted(
            buckets, key=lambda x: float("inf") if x == "+Inf" else float(x)
        )
    ]
    assert ordered == sorted(ordered)


@pytest.mark.asyncio
async def test_metrics_and_health_endpoints(async_client: AsyncClient, monkeypatch):
    monkeypatch.setattr("app.main.settings.operations_token", "test-operations-token")
    headers = {"Authorization": "Bearer test-operations-token"}
    assert (await async_client.get("/metrics")).status_code == 401
    metrics_resp = await async_client.get("/metrics", headers=headers)
    assert metrics_resp.status_code == 200
    assert "flexsearch_" in metrics_resp.text

    health = await async_client.get("/health/live")
    assert health.status_code == 200
    body = health.json()
    assert "status" in body
    assert body == {"status": "ok"}


# ---------------------------------------------------------------------------
# URL safety / SSRF
# ---------------------------------------------------------------------------


def test_ssrf_blocks_private_ips(monkeypatch):
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://127.0.0.1/admin")
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://10.0.0.5/secret")
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://localhost/x")
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://[::ffff:127.0.0.1]/")
    with pytest.raises(UnsafeURLError):
        assert_public_url("http://[::ffff:169.254.169.254]/latest/")
    monkeypatch.setattr(
        "app.services.url_safety.resolve_host_ips", lambda _host: ["93.184.216.34"]
    )
    assert is_safe_public_url("https://example.com/docs") is True


@pytest.mark.asyncio
async def test_crawl_rejects_private_url(async_client: AsyncClient, db_session):
    token = await create_user_and_login(async_client, "p5-ssrf@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    proj = await async_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "SSRF Proj", "description": "d"},
    )
    project_id = proj.json()["id"]

    resp = await async_client.post(
        f"/api/projects/{project_id}/crawl",
        headers=headers,
        json={"url": "http://127.0.0.1:8080/internal", "max_pages": 1},
    )
    assert resp.status_code == 400
    assert "Unsafe" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Job SSE ACL
# ---------------------------------------------------------------------------


def test_parse_project_id_from_crawl_job():
    pid = str(uuid4())
    job_id = f"crawl:{pid}:abc123"
    assert parse_project_id_from_job_id(job_id) == pid
    assert parse_project_id_from_job_id("bulk:deadbeef") is None


@pytest.mark.asyncio
async def test_job_sse_requires_project_access(async_client: AsyncClient, db_session):
    token_a = await create_user_and_login(async_client, "p5-owner@example.com")
    token_b = await create_user_and_login(async_client, "p5-other@example.com")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    proj = await async_client.post(
        "/api/projects",
        headers=headers_a,
        json={"name": "ACL Proj", "description": "d"},
    )
    project_id = proj.json()["id"]
    job_id = f"crawl:{project_id}:testhash"
    await register_job_meta(job_id, project_id=project_id, job_type="crawl")

    meta = await get_job_meta(job_id)
    assert meta is not None
    assert meta["project_id"] == project_id

    # Owner can open SSE (may hang on redis — we only check auth gate via 404/403)
    # Other user must get 403
    resp_b = await async_client.get(
        f"/api/jobs/{job_id}/events",
        headers=headers_b,
    )
    assert resp_b.status_code == 403

    # Unknown job → 404
    resp_missing = await async_client.get(
        f"/api/jobs/{uuid4()}/events",
        headers=headers_a,
    )
    assert resp_missing.status_code == 404


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_chat(async_client: AsyncClient, db_session):
    from app.core.config import settings

    token = await create_user_and_login(async_client, "p5-rl@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    proj = await async_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "RL Proj", "description": "d"},
    )
    project_id = proj.json()["id"]

    # Clear memory windows and temporarily tighten limit
    _memory._hits.clear()
    original = settings.rate_limit_chat_per_minute
    original_enabled = settings.rate_limit_enabled
    settings.rate_limit_enabled = True
    settings.rate_limit_chat_per_minute = 2
    try:
        with patch(
            "app.services.redis_client.get_redis",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with patch(
                "app.api.chat.ChatOrchestrator.answer",
                new_callable=AsyncMock,
            ) as mock_answer:
                from app.rag.chat.types import ChatAnswer

                mock_answer.return_value = ChatAnswer(
                    answer="ok",
                    citations=[],
                    retrieval_strategy="dense",
                    reranking_strategy="none",
                    empty_retrieval=True,
                    latency_ms=1,
                )
                with patch("app.api.chat._ensure_graph_ready"):
                    r1 = await async_client.post(
                        "/api/chat/query",
                        headers=headers,
                        json={
                            "project_id": project_id,
                            "query": "q1",
                            "persist": False,
                        },
                    )
                    r2 = await async_client.post(
                        "/api/chat/query",
                        headers=headers,
                        json={
                            "project_id": project_id,
                            "query": "q2",
                            "persist": False,
                        },
                    )
                    r3 = await async_client.post(
                        "/api/chat/query",
                        headers=headers,
                        json={
                            "project_id": project_id,
                            "query": "q3",
                            "persist": False,
                        },
                    )
        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        assert r3.status_code == 429, r3.text
    finally:
        settings.rate_limit_chat_per_minute = original
        settings.rate_limit_enabled = original_enabled
        _memory._hits.clear()


# ---------------------------------------------------------------------------
# Eval
# ---------------------------------------------------------------------------


def test_retrieval_at_k_and_faithfulness():
    scores = retrieval_at_k(
        ["a", "b", "c"],
        ["b", "z"],
        k=2,
    )
    assert scores["hit_at_k"] == 1.0
    assert scores["recall_at_k"] == 0.5
    faith = faithfulness_score(
        "FlexSearch uses OpenSearch",
        ["FlexSearch is an enterprise RAG platform with OpenSearch retrieval"],
    )
    assert faith > 0.5


def test_golden_eval_harness_passes_thresholds():
    report = run_eval(k=5)
    assert report.hit_at_k >= 0.8
    assert report.faithfulness >= 0.5
    assert "chunks_only" in report.by_mode
    assert "summaries_first" in report.by_mode or "mixed" in report.by_mode
