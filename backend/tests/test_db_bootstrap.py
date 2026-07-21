"""Regression tests for the explicit database/migration bootstrap."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import db_bootstrap


def test_bootstrap_derives_dev_admin_and_target_urls(monkeypatch) -> None:
    monkeypatch.setattr(db_bootstrap.settings, "app_env", "development")
    monkeypatch.setattr(db_bootstrap.settings, "postgres_admin_url", None)
    monkeypatch.setattr(
        db_bootstrap.settings,
        "postgres_url",
        "postgresql+asyncpg://app:secret@127.0.0.1:54321/flexsearch",
    )

    admin_dsn, migration_url, database = db_bootstrap._admin_and_migration_urls()

    assert admin_dsn.endswith("/postgres")
    assert migration_url.endswith("/flexsearch")
    assert database == "flexsearch"


def test_bootstrap_requires_explicit_admin_url_in_production(monkeypatch) -> None:
    monkeypatch.setattr(db_bootstrap.settings, "app_env", "production")
    monkeypatch.setattr(db_bootstrap.settings, "postgres_admin_url", None)

    with pytest.raises(RuntimeError, match="POSTGRES_ADMIN_URL is required"):
        db_bootstrap._admin_and_migration_urls()


@pytest.mark.asyncio
async def test_bootstrap_creates_missing_database_with_quoted_identifier(
    monkeypatch,
) -> None:
    executed: list[str] = []

    class FakeConnection:
        async def fetchval(self, _query, database):
            assert database == 'tenant"db'
            return None

        async def execute(self, query):
            executed.append(query)

        async def close(self):
            return None

    async def connect(_dsn):
        return FakeConnection()

    monkeypatch.setattr(db_bootstrap.asyncpg, "connect", connect)

    created = await db_bootstrap._create_database_if_missing(
        "postgresql://admin:secret@localhost/postgres",
        'tenant"db',
    )

    assert created is True
    assert executed == ['CREATE DATABASE "tenant""db"']


def test_celery_revision_check_disposes_async_pool(monkeypatch) -> None:
    from app import celery_app
    from app.db import postgres

    verify = AsyncMock()
    dispose = AsyncMock()
    monkeypatch.setattr(postgres, "init_db", verify)
    monkeypatch.setattr(postgres, "engine", SimpleNamespace(dispose=dispose))

    celery_app._verify_database_revision()

    verify.assert_awaited_once()
    dispose.assert_awaited_once()
