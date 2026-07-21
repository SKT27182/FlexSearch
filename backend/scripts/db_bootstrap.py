"""Create the application database if needed and apply Alembic migrations.

This is a deployment/development command, never an application-startup hook.
API and worker processes only verify the resulting revision and perform no DDL.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import asyncpg
from sqlalchemy.engine import URL, make_url

from app.core.config import settings


def _asyncpg_url(url: URL) -> str:
    driver = url.drivername.split("+", 1)[0]
    return url.set(drivername=driver).render_as_string(hide_password=False)


def _admin_and_migration_urls() -> tuple[str, str, str]:
    """Return admin DSN, target migration URL, and target database name."""
    target = make_url(settings.postgres_url)
    target_database = target.database or settings.postgres_db
    if not target_database:
        raise RuntimeError("POSTGRES_DB must name the application database")

    if settings.postgres_admin_url:
        admin = make_url(settings.postgres_admin_url)
    else:
        if settings.app_env == "production":
            raise RuntimeError(
                "POSTGRES_ADMIN_URL is required for production bootstrap"
            )
        admin = target.set(database="postgres")

    migration = admin.set(
        database=target_database,
        drivername=target.drivername,
    )
    return _asyncpg_url(admin), migration.render_as_string(hide_password=False), target_database


async def _create_database_if_missing(admin_dsn: str, database: str) -> bool:
    """Create the target database outside a transaction; return whether created."""
    connection = await asyncpg.connect(admin_dsn)
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            database,
        )
        if exists:
            return False
        quoted_database = '"' + database.replace('"', '""') + '"'
        await connection.execute(f"CREATE DATABASE {quoted_database}")
        return True
    finally:
        await connection.close()


def main() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    try:
        admin_dsn, migration_url, database = _admin_and_migration_urls()
        created = asyncio.run(_create_database_if_missing(admin_dsn, database))
    except Exception as exc:
        print(f"Failed to create or inspect application database: {exc}", file=sys.stderr)
        print(
            "Check POSTGRES_* match infra-hub and that POSTGRES_ADMIN_URL "
            "has CREATE DATABASE privileges. Do not reset infra volumes for "
            "an authentication or configuration error.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    if created:
        print(f'Created PostgreSQL database "{database}".')

    migration_env = os.environ.copy()
    migration_env["POSTGRES_URL"] = migration_url
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=backend_dir,
        env=migration_env,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)

    verify = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "current"],
        cwd=backend_dir,
        env=migration_env,
        check=False,
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0 or "009" not in verify.stdout:
        print(verify.stderr or verify.stdout, file=sys.stderr)
        raise SystemExit("Database migration did not reach required revision 009")
    print("FlexSearch database bootstrap complete at Alembic revision 009.")


if __name__ == "__main__":
    main()
