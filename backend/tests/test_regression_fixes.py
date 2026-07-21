"""Regression tests for reported config/import/switch issues."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models import Project, RagMode
from app.schemas.project import graph_backend_for_project
from app.services.litellm_config import vector_embedding_endpoint


def test_embedding_service_import_without_package_cycle() -> None:
    """app.rag.embedding must not re-export the service layer."""
    from app.rag import embedding as embedding_pkg

    assert "EmbeddingService" not in getattr(embedding_pkg, "__all__", [])
    from app.services.embedding import EmbeddingService

    assert EmbeddingService is not None


def test_settings_preserves_api_embedding_model_when_only_api_key_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        minio_access_key="a",
        minio_secret_key="s",
        jwt_secret="secret",
        embedding_model="openai/text-embedding-3-small",
        embedding_api_key="",
        api_key="sk-shared",
    )
    assert settings.embedding_model == "openai/text-embedding-3-small"
    monkeypatch.setattr("app.services.litellm_config.settings", settings)
    endpoint = vector_embedding_endpoint()
    assert endpoint.model == "openai/text-embedding-3-small"
    assert endpoint.is_local is False
    assert endpoint.api_key == "sk-shared"


def test_settings_downgrades_to_local_only_when_no_keys() -> None:
    settings = Settings(
        postgres_user="u",
        postgres_password="p",
        minio_access_key="a",
        minio_secret_key="s",
        jwt_secret="secret",
        embedding_model="openai/text-embedding-3-small",
        embedding_api_key="",
        api_key="",
    )
    assert settings.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"


async def _login(async_client: AsyncClient, email: str = "switch@example.com") -> str:
    await async_client.post(
        "/api/auth/register",
        json={"email": email, "name": "Switch User", "password": "password123"},
    )
    response = await async_client.post(
        "/api/auth/login",
        data={"username": email, "password": "password123"},
    )
    return response.json()["access_token"]


async def test_switch_graph_backend_within_graph_mode(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = await _login(async_client)
    create = await async_client.post(
        "/api/projects",
        json={"name": "Graph Switch", "rag_mode": "graph"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    project_id = create.json()["id"]

    response = await async_client.patch(
        f"/api/projects/{project_id}/rag-mode",
        json={"rag_mode": "graph", "graph_backend": "microsoft"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["generation"] == 2
    assert data["transition_status"] == "switching"

    result = await db_session.execute(
        select(Project).where(Project.id == UUID(project_id))
    )
    project = result.scalar_one()
    assert project.rag_mode == RagMode.GRAPH
    assert (
        graph_backend_for_project(project.rag_mode, project.rag_config) == "microsoft"
    )
    assert project.graph_index_status is not None
    assert project.graph_index_status.get("backend") == "microsoft"


async def test_switch_same_graph_backend_is_noop(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = await _login(async_client, "noop@example.com")
    create = await async_client.post(
        "/api/projects",
        json={"name": "Graph Noop", "rag_mode": "graph"},
        headers={"Authorization": f"Bearer {token}"},
    )
    project_id = create.json()["id"]

    response = await async_client.patch(
        f"/api/projects/{project_id}/rag-mode",
        json={"rag_mode": "graph", "graph_backend": "neo4j"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
