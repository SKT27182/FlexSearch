"""
FlexSearch Backend - PostgreSQL Connection

Async SQLAlchemy engine and session management.
"""

from collections.abc import AsyncGenerator

from app.utils.logger import create_logger
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql import text

from app.core.config import settings

logger = create_logger(__name__, level=settings.log_level)

# Create async engine.
# Never use echo=True: SQLAlchemy installs its own StreamHandler (plain white
# format) while the same events also propagate to our root/file handlers →
# duplicate lines. SQL statement logging is configured in logging_bridge.
engine = create_async_engine(
    settings.postgres_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

# Session factory
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base class."""

    pass


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session."""
    async with async_session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def _upgrade_user_name(conn) -> None:
    """Add name column and backfill from email local-part."""
    await conn.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS name VARCHAR(255);
            """
        )
    )
    await conn.execute(
        text(
            """
            UPDATE users
            SET name = split_part(email, '@', 1)
            WHERE name IS NULL OR name = '';
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE users
            ALTER COLUMN name SET NOT NULL;
            """
        )
    )


async def _upgrade_user_hierarchy(conn) -> None:
    """Add INFRA_ADMIN role and infra_hub_user_id for existing deployments."""
    await conn.execute(
        text(
            """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_type t
                    JOIN pg_enum e ON t.oid = e.enumtypid
                    WHERE t.typname = 'userrole' AND e.enumlabel = 'INFRA_ADMIN'
                ) THEN
                    ALTER TYPE userrole ADD VALUE 'INFRA_ADMIN';
                END IF;
            END
            $$;
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS infra_hub_user_id INTEGER;
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ix_users_infra_hub_user_id
            ON users (infra_hub_user_id)
            WHERE infra_hub_user_id IS NOT NULL;
            """
        )
    )


async def _upgrade_project_rag_config(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE projects
            ADD COLUMN IF NOT EXISTS rag_config JSONB NOT NULL DEFAULT '{}'::jsonb;
            """
        )
    )


async def _upgrade_document_processing(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS processing_step VARCHAR(255);
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS progress_pct INTEGER NOT NULL DEFAULT 0;
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS extracted_text_path VARCHAR(512);
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS extraction_config_hash VARCHAR(64);
            """
        )
    )
    await conn.execute(
        text(
            """
            ALTER TABLE documents
            ADD COLUMN IF NOT EXISTS extracted_at TIMESTAMPTZ;
            """
        )
    )


async def init_db() -> None:
    """Initialize database and required tables."""
    await ensure_database_exists()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _upgrade_user_hierarchy(conn)
        await _upgrade_user_name(conn)
        await _upgrade_project_rag_config(conn)
        await _upgrade_document_processing(conn)
        await conn.execute(text("DROP TABLE IF EXISTS token_usage"))
    logger.info("Database tables initialized")


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
    logger.info("Database connections closed")


async def ensure_database_exists() -> None:
    """Create target PostgreSQL database if it does not exist."""
    db_url = make_url(settings.postgres_url)
    if not db_url.drivername.startswith("postgresql") or not db_url.database:
        return

    target_database = db_url.database
    admin_url = db_url.set(database="postgres")
    admin_engine = create_async_engine(
        admin_url,
        echo=False,
        isolation_level="AUTOCOMMIT",
        pool_pre_ping=True,
    )

    try:
        async with admin_engine.connect() as conn:
            result = await conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
                {"db_name": target_database},
            )
            database_exists = result.scalar() is not None
            if database_exists:
                return

            escaped_database = target_database.replace('"', '""')
            await conn.execute(text(f'CREATE DATABASE "{escaped_database}"'))
            logger.info(f"Created database: {target_database}")
    finally:
        await admin_engine.dispose()
