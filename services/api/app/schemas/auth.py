"""Pydantic schemas for authentication."""
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: str
    password: str


class RefreshRequest(BaseModel):
    """Body for `POST /api/v1/auth/refresh` — redeem a refresh token."""
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
