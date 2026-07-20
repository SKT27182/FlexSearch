"""Create app database/schema if missing, then mark Alembic at head.

FlexSearch schema is owned by ``init_db`` (create_all + idempotent upgrades).
Alembic revisions exist for older deployments; on a fresh bootstrap they would
fight columns/tables already created by init_db, so we stamp head instead of
re-running them.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path


async def _init_schema() -> None:
    """Create DB + tables (FlexSearch schema is owned by init_db, not migration 001)."""
    # Models must be imported so Base.metadata is populated before create_all.
    import app.db.models  # noqa: F401
    from app.db.postgres import init_db

    await init_db()


def main() -> None:
    backend_dir = Path(__file__).resolve().parents[1]
    try:
        asyncio.run(_init_schema())
    except Exception as exc:
        print(f"Failed to initialize database schema: {exc}", file=sys.stderr)
        print(
            "Check POSTGRES_* in backend/.env match infra-hub. "
            "If the Postgres password changed, recreate infra volumes:\n"
            "  cd ../infra-hub && make clean-hard && make up",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    # Schema is current via init_db; record Alembic head without re-applying DDL.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "stamp", "head"],
        cwd=backend_dir,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    print("FlexSearch database bootstrap complete.")


if __name__ == "__main__":
    main()
