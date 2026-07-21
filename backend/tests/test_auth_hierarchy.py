"""Tests for infra-hub vs FlexSearch-local authentication."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import User, UserRole
from app.services.auth_login import authenticate_user, get_or_create_infra_linked_user
from app.services.infra_hub_users import InfraHubUser


@pytest.mark.asyncio
async def test_authenticate_infra_hub_user_creates_linked_row():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    infra = InfraHubUser(
        id=1,
        email="admin@infra.local",
        name="ADMIN",
        hashed_password="hash",
        is_active=True,
    )

    with patch(
        "app.services.auth_login.verify_infra_hub_credentials",
        new=AsyncMock(return_value=infra),
    ):
        user = await authenticate_user(db, "admin@infra.local", "secret")

    assert user is not None
    assert user.role == UserRole.INFRA_ADMIN
    assert user.infra_hub_user_id == 1
    db.add.assert_called_once()
    db.commit.assert_called()


@pytest.mark.asyncio
async def test_infra_linked_user_cannot_use_local_password():
    db = AsyncMock()
    local = User(
        email="admin@infra.local",
        name="ADMIN",
        hashed_password="local-hash",
        role=UserRole.INFRA_ADMIN,
        infra_hub_user_id=1,
    )
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: local))

    with (
        patch(
            "app.services.auth_login.verify_infra_hub_credentials",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.auth_login.verify_password",
            return_value=True,
        ),
    ):
        user = await authenticate_user(db, "admin@infra.local", "secret")

    assert user is None


@pytest.mark.asyncio
async def test_get_or_create_updates_existing_to_infra_admin():
    db = AsyncMock()
    existing = User(
        email="admin@infra.local",
        name="Admin",
        hashed_password="x",
        role=UserRole.USER,
        infra_hub_user_id=None,
    )
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: existing))
    infra = InfraHubUser(
        id=42,
        email="admin@infra.local",
        name="ADMIN",
        hashed_password="hash",
        is_active=True,
    )

    user = await get_or_create_infra_linked_user(db, infra)

    assert user.role == UserRole.INFRA_ADMIN
    assert user.infra_hub_user_id == 42
    db.commit.assert_called()
