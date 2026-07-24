"""Tests for graph export zip endpoint."""

import io
import zipfile
from unittest.mock import MagicMock

import networkx as nx
import pandas as pd
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Project
from app.schemas.graph_index import GraphIndexState
from app.schemas.rag_config import GraphRagConfig


async def _login(client: AsyncClient, email: str) -> str:
    await client.post(
        "/api/auth/register",
        json={"email": email, "name": "Export User", "password": "password123"},
    )
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "password123"},
    )
    return resp.json()["access_token"]


async def _create_microsoft_graph_project(
    client: AsyncClient, token: str, name: str
) -> str:
    create = await client.post(
        "/api/projects",
        json={
            "name": name,
            "rag_mode": "graph",
            "rag_config": GraphRagConfig.from_settings(
                graph_backend="microsoft"
            ).to_db(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201
    return create.json()["id"]


@pytest.mark.asyncio
async def test_graph_export_requires_ready_index(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    token = await _login(async_client, "export@example.com")
    project_id = await _create_microsoft_graph_project(
        async_client, token, "Graph Project"
    )
    response = await async_client.get(
        f"/api/projects/{project_id}/graph-export",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_graph_export_zip_contents(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    token = await _login(async_client, "export2@example.com")
    project_id = await _create_microsoft_graph_project(
        async_client, token, "Graph Ready"
    )

    from uuid import UUID

    project = await db_session.get(Project, UUID(project_id))
    assert project is not None
    project.graph_index_status = GraphIndexState(
        backend="microsoft", status="ready", document_count=1
    ).to_db()
    await db_session.commit()

    storage = MagicMock()
    storage.file_exists.side_effect = lambda key: key.endswith("entities.parquet")
    storage.download_file.return_value = b"parquet-bytes"
    storage.list_files.return_value = []
    monkeypatch.setattr("app.api.projects.get_storage_service", lambda: storage)

    response = await async_client.get(
        f"/api/projects/{project_id}/graph-export",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(response.content))
    assert "entities.parquet" in zf.namelist()


@pytest.mark.asyncio
async def test_graph_export_regenerates_graphml_from_current_parquets(
    async_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch,
) -> None:
    token = await _login(async_client, "export3@example.com")
    project_id = await _create_microsoft_graph_project(
        async_client, token, "Graph Current"
    )

    from uuid import UUID

    project = await db_session.get(Project, UUID(project_id))
    assert project is not None
    project.graph_index_status = GraphIndexState(
        backend="microsoft", status="ready", document_count=2
    ).to_db()
    await db_session.commit()

    entities_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {"id": "entity-1", "title": "PROJECT ORION", "type": "PROJECT"},
            {"id": "entity-2", "title": "NEO4J", "type": "TECHNOLOGY"},
        ]
    ).to_parquet(entities_buffer, index=False)
    relationships_buffer = io.BytesIO()
    pd.DataFrame(
        [
            {
                "id": "relationship-1",
                "source": "PROJECT ORION",
                "target": "NEO4J",
                "weight": 8.0,
            }
        ]
    ).to_parquet(relationships_buffer, index=False)

    payloads = {
        "entities.parquet": entities_buffer.getvalue(),
        "relationships.parquet": relationships_buffer.getvalue(),
        "graph.graphml": b"<stale-graphml />",
    }
    storage = MagicMock()
    storage.file_exists.side_effect = lambda key: key.rsplit("/", 1)[-1] in payloads
    storage.download_file.side_effect = lambda key: payloads[key.rsplit("/", 1)[-1]]
    monkeypatch.setattr("app.api.projects.get_storage_service", lambda: storage)

    response = await async_client.get(
        f"/api/projects/{project_id}/graph-export",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        graph = nx.parse_graphml(zf.read("graph.graphml").decode("utf-8"))
    assert set(graph.nodes) == {"PROJECT ORION", "NEO4J"}
    assert graph.has_edge("PROJECT ORION", "NEO4J")
