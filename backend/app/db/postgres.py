"""
FlexSearch Backend - PostgreSQL Connection

Async SQLAlchemy engine and session management.
"""

from collections.abc import AsyncGenerator

from app.utils.logger import create_logger
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


async def init_db() -> None:
    """Verify the externally migrated schema; application startup never runs DDL."""
    if not settings.postgres_url.startswith("postgresql"):
        return
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        revision = result.scalar_one_or_none()
    if revision != "009":
        raise RuntimeError(
            f"Database revision {revision!r} does not match required revision '009'; "
            "run `alembic upgrade head` before starting FlexSearch"
        )
    logger.info("Database schema revision verified: %s", revision)


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()
    logger.info("Database connections closed")
