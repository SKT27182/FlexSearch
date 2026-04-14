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

logger = create_logger(__name__)

# Create async engine
engine = create_async_engine(
    settings.postgres_url,
    echo=settings.debug,
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


async def init_db() -> None:
    """Initialize database and required tables."""
    await ensure_database_exists()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Cleanup legacy table removed in retrieval-only mode.
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
        echo=settings.debug,
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
