"""
FlexSearch Backend - Auth Schemas

Pydantic models for authentication endpoints.
"""

from uuid import UUID
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    """User registration request."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class UserLogin(BaseModel):
    """User login request."""

    email: EmailStr
    password: str


class Token(BaseModel):
    """JWT token response."""

    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """JWT token payload."""

    sub: str  # user_id
    role: str
    exp: int


class UserResponse(BaseModel):
    """User response model."""

    id: UUID
    email: EmailStr
    role: str
    created_at: datetime

    class Config:
        from_attributes = True
