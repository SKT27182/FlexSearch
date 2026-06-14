"""Tests for destructive rag_mode switch."""

from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def _login(client: AsyncClient, email: str) -> str:
    await client.post(
        "/api/auth/register",
        json={"email": email, "name": "Mode User", "password": "password123"},
    )
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "password123"},
    )
    return resp.json()["access_token"]


@pytest.mark.asyncio
async def test_switch_vector_to_graph(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    token = await _login(async_client, "switch@example.com")
    create = await async_client.post(
        "/api/projects",
        json={"name": "Switch Me", "rag_mode": "vector"},
        headers={"Authorization": f"Bearer {token}"},
    )
    project_id = create.json()["id"]

    vector_store = MagicMock()
    storage = MagicMock()
    monkeypatch.setattr(
        "app.services.project_index_service.get_vector_store",
        lambda: vector_store,
    )
    monkeypatch.setattr(
        "app.services.project_index_service.get_storage_service",
        lambda: storage,
    )
    scheduled: list = []
    monkeypatch.setattr(
        "app.api.projects.schedule_process_document",
        lambda *args, **kwargs: scheduled.append(args),
    )

    response = await async_client.patch(
        f"/api/projects/{project_id}/rag-mode",
        json={"rag_mode": "graph"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["rag_mode"] == "graph"
    vector_store.delete_by_project.assert_called_once_with(project_id)

    get_resp = await async_client.get(
        f"/api/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert get_resp.json()["rag_mode"] == "graph"
    assert get_resp.json()["graph_index_status"]["status"] == "pending"
