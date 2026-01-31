"""
FlexSearch Backend - Projects API Tests
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession


async def create_user_and_login(client: AsyncClient, email: str = "test@example.com"):
    """Helper to create user and get token."""
    await client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123"},
    )
    response = await client.post(
        "/api/auth/login",
        data={"username": email, "password": "password123"},
    )
    return response.json()["access_token"]


class TestProjectsCRUD:
    """Test project CRUD operations."""

    async def test_create_project(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Should create a new project."""
        token = await create_user_and_login(async_client)

        response = await async_client.post(
            "/api/projects",
            json={"name": "Test Project", "description": "A test project"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Project"
        assert data["description"] == "A test project"
        assert "id" in data

    async def test_list_projects(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Should list user's projects."""
        token = await create_user_and_login(async_client)

        # Create two projects
        await async_client.post(
            "/api/projects",
            json={"name": "Project 1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        await async_client.post(
            "/api/projects",
            json={"name": "Project 2"},
            headers={"Authorization": f"Bearer {token}"},
        )

        response = await async_client.get(
            "/api/projects",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    async def test_get_project(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Should get a specific project."""
        token = await create_user_and_login(async_client)

        create_response = await async_client.post(
            "/api/projects",
            json={"name": "My Project"},
            headers={"Authorization": f"Bearer {token}"},
        )
        project_id = create_response.json()["id"]

        response = await async_client.get(
            f"/api/projects/{project_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "My Project"

    async def test_update_project(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Should update a project."""
        token = await create_user_and_login(async_client)

        create_response = await async_client.post(
            "/api/projects",
            json={"name": "Old Name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        project_id = create_response.json()["id"]

        response = await async_client.patch(
            f"/api/projects/{project_id}",
            json={"name": "New Name"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "New Name"

    async def test_delete_project(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Should delete a project."""
        token = await create_user_and_login(async_client)

        create_response = await async_client.post(
            "/api/projects",
            json={"name": "To Delete"},
            headers={"Authorization": f"Bearer {token}"},
        )
        project_id = create_response.json()["id"]

        response = await async_client.delete(
            f"/api/projects/{project_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 204

        # Verify deleted
        get_response = await async_client.get(
            f"/api/projects/{project_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert get_response.status_code == 404


class TestProjectsOwnership:
    """Test project ownership enforcement."""

    async def test_cannot_access_other_users_project(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """User cannot access another user's project."""
        token1 = await create_user_and_login(async_client, "user1@example.com")
        token2 = await create_user_and_login(async_client, "user2@example.com")

        # User 1 creates project
        create_response = await async_client.post(
            "/api/projects",
            json={"name": "User1's Project"},
            headers={"Authorization": f"Bearer {token1}"},
        )
        project_id = create_response.json()["id"]

        # User 2 tries to access
        response = await async_client.get(
            f"/api/projects/{project_id}",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert response.status_code == 403
