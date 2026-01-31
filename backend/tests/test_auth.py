"""
FlexSearch Backend - Authentication Tests
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User


class TestAuthRegister:
    """Test user registration endpoint."""

    async def test_register_first_user_is_admin(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """First registered user should be ADMIN."""
        response = await async_client.post(
            "/api/auth/register",
            json={"email": "first@example.com", "password": "password123"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["email"] == "first@example.com"
        assert data["role"] == "ADMIN"

    async def test_register_second_user_is_regular(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Subsequent users should be USER role."""
        # First user
        await async_client.post(
            "/api/auth/register",
            json={"email": "admin@example.com", "password": "password123"},
        )
        # Second user
        response = await async_client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "password123"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["role"] == "USER"

    async def test_register_duplicate_email_fails(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Duplicate email should fail."""
        await async_client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "password": "password123"},
        )
        response = await async_client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "password": "password123"},
        )
        assert response.status_code == 400

    async def test_register_weak_password_fails(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Password too short should fail."""
        response = await async_client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "password": "short"},
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
            json={"email": "login@example.com", "password": "password123"},
        )
        # Login
        response = await async_client.post(
            "/api/auth/login",
            data={"username": "login@example.com", "password": "password123"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    async def test_login_invalid_password(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Invalid password should fail."""
        await async_client.post(
            "/api/auth/register",
            json={"email": "test@example.com", "password": "password123"},
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
            json={"email": "me@example.com", "password": "password123"},
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

    async def test_me_unauthenticated(
        self, async_client: AsyncClient, db_session: AsyncSession
    ):
        """Unauthenticated request should fail."""
        response = await async_client.get("/api/auth/me")
        assert response.status_code == 401
