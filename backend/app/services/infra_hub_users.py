"""Read-only access to infra-hub users in main_db (no data copied to FlexSearch)."""

from dataclasses import dataclass

import asyncpg

from app.core.config import settings
from app.core.security import verify_password
from app.utils.logger import create_logger

logger = create_logger(__name__)


@dataclass(frozen=True)
class InfraHubUser:
    """Minimal view of an infra-hub main_db user."""

    id: int
    email: str
    hashed_password: str
    is_active: bool


async def get_infra_hub_user_by_email(email: str) -> InfraHubUser | None:
    """Return infra-hub user if present in main_db.users."""
    try:
        conn = await asyncpg.connect(settings.infra_hub_postgres_url)
        try:
            row = await conn.fetchrow(
                """
                SELECT id, email, hashed_password, is_active
                FROM users
                WHERE email = $1
                """,
                email,
            )
        finally:
            await conn.close()
    except Exception as exc:
        logger.error(f"Failed to query infra-hub users: {exc}")
        return None

    if row is None:
        return None
    return InfraHubUser(
        id=row["id"],
        email=row["email"],
        hashed_password=row["hashed_password"],
        is_active=row["is_active"],
    )


async def verify_infra_hub_credentials(email: str, password: str) -> InfraHubUser | None:
    """True when email exists in main_db.users and password matches."""
    user = await get_infra_hub_user_by_email(email)
    if user is None or not user.is_active:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user
