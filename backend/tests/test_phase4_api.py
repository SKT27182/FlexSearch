"""Phase 4 API smoke tests (crawl/bulk/suggestions endpoints)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import OutboxEvent


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


@pytest.mark.asyncio
async def test_crawl_submit_endpoint(async_client: AsyncClient, db_session):
    token = await create_user_and_login(async_client, "phase4-crawl@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    proj = await async_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "Crawl Proj", "description": "d"},
    )
    assert proj.status_code == 201, proj.text
    project_id = proj.json()["id"]

    with patch(
        "app.services.url_safety.resolve_host_ips", return_value=["93.184.216.34"]
    ):
        resp = await async_client.post(
            f"/api/projects/{project_id}/crawl",
            headers=headers,
            json={"url": "https://example.com/docs", "max_pages": 5},
        )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"].startswith(f"crawl:{project_id}:")
    assert body["project_id"] == project_id
    event = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "website_crawl")
        )
    ).scalar_one()
    assert event.payload["job_id"] == body["job_id"]


@pytest.mark.asyncio
async def test_suggestions_endpoint_mocked(async_client: AsyncClient, db_session):
    token = await create_user_and_login(async_client, "phase4-suggest@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    proj = await async_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "Suggest Proj", "description": "d"},
    )
    project_id = proj.json()["id"]

    with patch(
        "app.api.jobs.generate_project_suggestions",
        return_value=["What is FlexSearch?", "How does crawl work?"],
    ):
        resp = await async_client.get(
            f"/api/projects/{project_id}/suggestions",
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["questions"]) == 2


@pytest.mark.asyncio
async def test_bulk_import_rejects_bad_extension(async_client: AsyncClient, db_session):
    token = await create_user_and_login(async_client, "phase4-bulk@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    proj = await async_client.post(
        "/api/projects",
        headers=headers,
        json={"name": "Bulk Proj", "description": "d"},
    )
    project_id = proj.json()["id"]

    resp = await async_client.post(
        f"/api/projects/{project_id}/bulk-import",
        headers=headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
