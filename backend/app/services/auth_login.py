"""Authentication helpers: infra-hub main_db vs FlexSearch-local users."""

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash, verify_password
from app.db.models import User, UserRole
from app.services.infra_hub_users import InfraHubUser, verify_infra_hub_credentials
from app.utils.logger import create_logger

logger = create_logger(__name__)

# Placeholder hash for infra-linked rows (auth always via main_db).
_UNUSABLE_PASSWORD_HASH = get_password_hash(secrets.token_urlsafe(32))


async def get_or_create_infra_linked_user(
    db: AsyncSession,
    infra_user: InfraHubUser,
) -> User:
    """Ensure a FlexSearch row exists for an infra-hub user (link only)."""
    result = await db.execute(select(User).where(User.email == infra_user.email))
    user = result.scalar_one_or_none()

    if user is None:
        user = User(
            email=infra_user.email,
            name=infra_user.name,
            hashed_password=_UNUSABLE_PASSWORD_HASH,
            role=UserRole.INFRA_ADMIN,
            infra_hub_user_id=infra_user.id,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info(f"Linked infra-hub admin to FlexSearch: {infra_user.email}")
        return user

    user.role = UserRole.INFRA_ADMIN
    user.infra_hub_user_id = infra_user.id
    user.name = infra_user.name
    await db.commit()
    await db.refresh(user)
    return user


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> User | None:
    """
    Authenticate with hierarchy:
    1. Any active infra-hub main_db user (read-only) -> INFRA_ADMIN in FlexSearch
    2. FlexSearch-local users -> USER or ADMIN

    No admin email/password is configured in FlexSearch; infra-hub is the source
    of truth for platform admins (infra-hub is admin-only).
    """
    infra_user = await verify_infra_hub_credentials(email, password)
    if infra_user is not None:
        logger.debug("Authenticated via infra-hub for %s", email)
        return await get_or_create_infra_linked_user(db, infra_user)

    result = await db.execute(select(User).where(User.email == email))
    local_user = result.scalar_one_or_none()
    if local_user is None:
        return None

    # Infra-linked accounts must authenticate through main_db only.
    if local_user.infra_hub_user_id is not None:
        return None

    if not verify_password(password, local_user.hashed_password):
        logger.debug("Local auth failed for %s", email)
        return None
    logger.debug("Authenticated via FlexSearch-local for %s", email)
    return local_user
