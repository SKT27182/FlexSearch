"""
FlexSearch Backend - Auth API Router

Authentication endpoints: register, login, refresh, me.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_db
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.models import User, UserRole
from app.schemas.auth import Token, UserRegister, UserResponse
from app.utils.logger import create_logger

logger = create_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(
    user_data: UserRegister,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    """
    Register a new user.

    First user is automatically promoted to ADMIN.
    """
    # Check if email already exists
    result = await db.execute(select(User).where(User.email == user_data.email))
    existing_user = result.scalar_one_or_none()

    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Check if this is the first user (should be ADMIN)
    result = await db.execute(select(func.count()).select_from(User))
    user_count = result.scalar() or 0

    role = UserRole.ADMIN if user_count == 0 else UserRole.USER

    # Create user
    user = User(
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        role=role,
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)

    logger.info(f"User registered: {user.email} with role {role}")

    return user


@router.post("/login", response_model=Token)
async def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Token:
    """
    Login with email and password.

    Returns JWT access token.
    """
    # Find user by email
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create access token
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role.value}
    )

    logger.info(f"User logged in: {user.email}")

    return Token(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Get current user information."""
    return current_user
