"""
FlexSearch Backend - Retrieval API Tests
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.retrieval.base import RetrievalResult


async def create_user_and_login(client: AsyncClient, email: str) -> str:
    """Create a user and return access token."""
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123"},
    )
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "password123"},
    )
    return response.json()["access_token"]


class TestRetrievalQuery:
    """Test retrieval-only query endpoint."""

    async def test_query_requires_auth(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        response = await async_client.post(
            "/api/retrieval/query",
            json={
                "project_id": "6c8ac9f8-df5c-4f4d-bc8c-cfe6608f9cf8",
                "query": "test query",
                "top_k": 5,
            },
        )
        assert response.status_code == 401

    async def test_query_returns_chunks(
        self, async_client: AsyncClient, db_session: AsyncSession, monkeypatch
    ) -> None:
        token = await create_user_and_login(async_client, "retrieval@example.com")
        project_response = await async_client.post(
            "/api/projects",
            json={"name": "Retrieval Project"},
            headers={"Authorization": f"Bearer {token}"},
        )
        project_id = project_response.json()["id"]

        class FakePipeline:
            retrieval_strategy = "dense"

            async def retrieve(
                self, query: str, project_id: str, top_k: int = 5
            ) -> list[RetrievalResult]:
                return [
                    RetrievalResult(
                        content="First chunk",
                        score=0.92,
                        document_id="doc-1",
                        chunk_id="chunk-1",
                        metadata={"filename": "test.pdf", "chunk_index": 0},
                    ),
                    RetrievalResult(
                        content="Second chunk",
                        score=0.84,
                        document_id="doc-2",
                        chunk_id="chunk-2",
                        metadata={"filename": "test2.pdf", "chunk_index": 3},
                    ),
                ][:top_k]

        monkeypatch.setattr(
            "app.api.retrieval.get_rag_pipeline",
            lambda: FakePipeline(),
        )

        response = await async_client.post(
            "/api/retrieval/query",
            json={
                "project_id": project_id,
                "query": "what is in docs",
                "top_k": 2,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["project_id"] == project_id
        assert payload["query"] == "what is in docs"
        assert payload["retrieval_strategy"] == "dense"
        assert payload["total"] == 2
        assert len(payload["chunks"]) == 2
        assert payload["chunks"][0]["chunk_id"] == "chunk-1"
        assert payload["chunks"][0]["metadata"]["filename"] == "test.pdf"

    async def test_query_forbidden_for_other_user_project(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        owner_token = await create_user_and_login(async_client, "owner@example.com")
        attacker_token = await create_user_and_login(
            async_client, "attacker@example.com"
        )

        project_response = await async_client.post(
            "/api/projects",
            json={"name": "Private Project"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        project_id = project_response.json()["id"]

        response = await async_client.post(
            "/api/retrieval/query",
            json={"project_id": project_id, "query": "leak data", "top_k": 3},
            headers={"Authorization": f"Bearer {attacker_token}"},
        )
        assert response.status_code == 403

    async def test_query_missing_project(
        self, async_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        token = await create_user_and_login(async_client, "missing@example.com")
        response = await async_client.post(
            "/api/retrieval/query",
            json={
                "project_id": "6c8ac9f8-df5c-4f4d-bc8c-cfe6608f9cf8",
                "query": "missing",
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
