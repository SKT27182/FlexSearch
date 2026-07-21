"""
FlexSearch Backend - FastAPI Dependencies

Reusable dependencies for authentication, database sessions, etc.
"""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import decode_access_token
from app.db.models import User, UserRole
from app.db.postgres import get_session
from app.utils.logger import create_logger

logger = create_logger(__name__, level=settings.log_level)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_db() -> AsyncSession:
    """Get database session dependency."""
    async for session in get_session():
        yield session


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Get current authenticated user from JWT token."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None:
        logger.debug("Auth rejected: invalid token")
        raise credentials_exception

    user_id_str: str | None = payload.get("sub")
    token_version = payload.get("ver")
    if user_id_str is None or not isinstance(token_version, int):
        logger.debug("Auth rejected: missing subject in token")
        raise credentials_exception

    try:
        user_uuid = uuid.UUID(user_id_str)
    except ValueError:
        logger.debug("Auth rejected: invalid user id in token")
        raise credentials_exception from None

    from sqlalchemy import select

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if user is None:
        logger.debug("Auth rejected: user not found")
        raise credentials_exception
    if user.token_version != token_version:
        logger.debug("Auth rejected: revoked token version")
        raise credentials_exception

    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Get current active user."""
    return current_user


def is_infra_admin(user: User) -> bool:
    return user.role == UserRole.INFRA_ADMIN


def is_flexsearch_admin(user: User) -> bool:
    """FlexSearch-scoped admin (not infra-hub)."""
    return user.role == UserRole.ADMIN


def has_admin_access(user: User) -> bool:
    return user.role in (UserRole.INFRA_ADMIN, UserRole.ADMIN)


async def require_admin(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Require FlexSearch admin or infra-hub admin."""
    if not has_admin_access(current_user):
        logger.warning("Admin access denied for user %s", current_user.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


async def require_infra_admin(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Require infra-hub admin."""
    if not is_infra_admin(current_user):
        logger.warning("Infra admin required, denied for %s", current_user.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Infra-hub admin privileges required",
        )
    return current_user
