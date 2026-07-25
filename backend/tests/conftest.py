"""
FlexSearch Backend - Test Fixtures
"""

from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from tests._bootstrap import TEST_ROOT

from app.main import app
from app.db.models import Base
from app.core.dependencies import get_db
from app.core.config import settings
from app.core.rate_limit import _memory


@pytest.fixture(scope="session")
def test_root_path():
    return TEST_ROOT


@pytest.fixture(autouse=True)
def isolate_process_state(monkeypatch):
    """Unit tests never call shared auth/Redis state or leak rate-limit windows."""

    async def no_infra_user(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.auth_login.verify_infra_hub_credentials", no_infra_user
    )
    original_rate_limit = settings.rate_limit_enabled
    settings.rate_limit_enabled = False
    _memory.clear()
    yield
    settings.rate_limit_enabled = original_rate_limit
    _memory.clear()


@pytest_asyncio.fixture(scope="function")
async def db_session(tmp_path) -> AsyncGenerator[AsyncSession, None]:
    """Create a test database session."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        poolclass=NullPool,
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client for testing."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client

    app.dependency_overrides.clear()
