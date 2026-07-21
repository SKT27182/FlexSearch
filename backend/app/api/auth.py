"""
FlexSearch Backend - Auth API Router

Authentication endpoints: register, login, me, profile.
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_db
from app.core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.core.rate_limit import LOGIN_RULE, REGISTER_RULE, check_rate_limit
from app.db.models import User, UserRole
from app.schemas.auth import (
    PasswordChange,
    ProfileUpdate,
    Token,
    UserRegister,
    UserResponse,
)
from app.services.auth_login import authenticate_user
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    user_data: UserRegister,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Register a FlexSearch-local user (always USER role)."""
    await check_rate_limit(request, REGISTER_RULE)
    result = await db.execute(select(User).where(User.email == user_data.email))
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=user_data.email,
        name=user_data.name.strip(),
        hashed_password=get_password_hash(user_data.password),
        role=UserRole.USER,
        infra_hub_user_id=None,
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"User registered: {user.email}")

    return user


@router.post("/login", response_model=Token)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Token:
    """
    Login with email and password.

    Infra-hub main_db users authenticate first and receive INFRA_ADMIN.
    """
    await check_rate_limit(request, LOGIN_RULE)
    await check_rate_limit(
        request, LOGIN_RULE, user_id=f"account:{form_data.username.strip().lower()}"
    )
    user = await authenticate_user(db, form_data.username, form_data.password)
    if user is None:
        logger.debug("Login failed for %s", form_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role.value, "ver": user.token_version}
    )

    logger.info(f"User logged in: {user.email} ({user.role.value})")

    return Token(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Get current user information."""
    return current_user


@router.patch("/me/profile", response_model=UserResponse)
async def update_profile(
    body: ProfileUpdate,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """Update display name (email cannot be changed)."""
    if current_user.infra_hub_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Infra-hub linked accounts must update name in Infra Hub",
        )
    current_user.name = body.name.strip()
    current_user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(current_user)
    logger.info("Profile updated for user %s", current_user.email)
    return current_user


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChange,
    current_user: Annotated[User, Depends(get_current_active_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """Change password for local FlexSearch accounts."""
    if current_user.infra_hub_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Infra-hub linked accounts must change password in Infra Hub",
        )
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    current_user.hashed_password = get_password_hash(body.new_password)
    current_user.token_version += 1
    current_user.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Password changed for user %s", current_user.email)
