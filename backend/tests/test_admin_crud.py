"""Admin CRUD role rules."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.db.models import User, UserRole
from app.api import admin as admin_api


@pytest.mark.asyncio
async def test_delete_user_rejects_admin_target_from_app_admin():
    current = User(
        id=uuid.uuid4(),
        email="admin@local.com",
        name="Admin",
        hashed_password="x",
        role=UserRole.ADMIN,
    )
    target_id = uuid.uuid4()
    target = User(
        id=target_id,
        email="other@local.com",
        name="Other",
        hashed_password="x",
        role=UserRole.ADMIN,
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: target)
    )

    with pytest.raises(HTTPException) as exc:
        await admin_api.delete_user(target_id, current, db)

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_user_rejects_infra_admin_target():
    current = User(
        id=uuid.uuid4(),
        email="infra@infra.local",
        name="Infra",
        hashed_password="x",
        role=UserRole.INFRA_ADMIN,
        infra_hub_user_id=1,
    )
    target_id = uuid.uuid4()
    target = User(
        id=target_id,
        email="linked@infra.local",
        name="Linked",
        hashed_password="x",
        role=UserRole.INFRA_ADMIN,
        infra_hub_user_id=2,
    )
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: target)
    )

    with pytest.raises(HTTPException) as exc:
        await admin_api.delete_user(target_id, current, db)

    assert exc.value.status_code == 403
