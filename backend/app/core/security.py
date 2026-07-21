"""
FlexSearch Backend - Security utilities

Password hashing and JWT token management.
"""

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
import bcrypt
import jwt
from jwt import PyJWTError

from app.core.config import settings
from app.utils.logger import create_logger

logger = create_logger(__name__)

password_hasher = PasswordHasher()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    if hashed_password.startswith("$argon2"):
        try:
            return password_hasher.verify(hashed_password, plain_password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False
    if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"), hashed_password.encode("ascii")
            )
        except (TypeError, ValueError):
            return False
    return False


def get_password_hash(password: str) -> str:
    """Hash a password."""
    return password_hasher.hash(password)


def password_hash_needs_update(hashed_password: str) -> bool:
    if not hashed_password.startswith("$argon2"):
        return True
    try:
        return password_hasher.check_needs_rehash(hashed_password)
    except InvalidHashError:
        return True


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.jwt_expire_minutes
        )

    now = datetime.now(timezone.utc)
    to_encode.update({"exp": expire, "iat": now, "jti": str(uuid.uuid4())})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT access token."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except PyJWTError as e:
        logger.warning(f"JWT decode error: {e}")
        return None
