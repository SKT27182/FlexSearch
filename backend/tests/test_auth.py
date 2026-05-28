"""
FlexSearch Backend - Authentication Tests
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class TestAuthRegister:
    """Test user registration endpoint."""

    async def test_register_creates_regular_user(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Registration always creates USER role (admins are promoted by infra-hub)."""
        response = await async_client.post(
            "/api/auth/register",
            json={
                "email": "first@example.com",
                "name": "First User",
                "password": "password123",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "first@example.com"
        assert data["name"] == "First User"
        assert data["role"] == "USER"

    async def test_register_duplicate_email_fails(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Duplicate email should fail."""
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "name": "Test",
                "password": "password123",
            },
        )
        response = await async_client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "name": "Test",
                "password": "password123",
            },
        )
        assert response.status_code == 400
        assert "already registered" in response.json()["detail"].lower()

    async def test_register_weak_password_fails(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Password too short should fail."""
        response = await async_client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "name": "Test", "password": "short"},
        )
        assert response.status_code == 422


class TestAuthLogin:
    """Test user login endpoint."""

    async def test_login_success(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Valid credentials should return tokens."""
        # Register first
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "login@example.com",
                "name": "Login User",
                "password": "password123",
            },
        )
        # Login
        response = await async_client.post(
            "/api/auth/login",
            data={"username": "login@example.com", "password": "password123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_invalid_password(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Invalid password should fail."""
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "test@example.com",
                "name": "Test",
                "password": "password123",
            },
        )
        response = await async_client.post(
            "/api/auth/login",
            data={"username": "test@example.com", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    async def test_login_nonexistent_user(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Nonexistent user should fail."""
        response = await async_client.post(
            "/api/auth/login",
            data={"username": "nobody@example.com", "password": "password123"},
        )
        assert response.status_code == 401


class TestAuthMe:
    """Test current user endpoint."""

    async def test_me_authenticated(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Authenticated user should get their info."""
        # Register and login
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "me@example.com",
                "name": "Me User",
                "password": "password123",
            },
        )
        login = await async_client.post(
            "/api/auth/login",
            data={"username": "me@example.com", "password": "password123"},
        )
        token = login.json()["access_token"]

        # Get me
        response = await async_client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["email"] == "me@example.com"
        assert data["name"] == "Me User"
        assert "updated_at" in data

    async def test_me_unauthenticated(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Unauthenticated request should fail."""
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 401


class TestAuthProfile:
    """Profile and password endpoints."""

    async def _register_and_token(self, async_client: AsyncClient) -> str:
        await async_client.post(
            "/api/auth/register",
            json={
                "email": "profile@example.com",
                "name": "Original",
                "password": "password123",
            },
        )
        login = await async_client.post(
            "/api/auth/login",
            data={"username": "profile@example.com", "password": "password123"},
        )
        return login.json()["access_token"]

    async def test_patch_profile_updates_name(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        token = await self._register_and_token(async_client)
        response = await async_client.patch(
            "/api/auth/me/profile",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "Updated Name"},
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    async def test_change_password_bumps_access(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        token = await self._register_and_token(async_client)
        response = await async_client.post(
            "/api/auth/me/password",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "current_password": "password123",
                "new_password": "newpassword99",
            },
        )
        assert response.status_code == 204
        login = await async_client.post(
            "/api/auth/login",
            data={"username": "profile@example.com", "password": "newpassword99"},
        )
        assert login.status_code == 200
