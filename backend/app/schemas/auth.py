"""Authentication request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints

from app.models import InvitationStatus, UserRole, UserStatus

Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=64)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=320)]
Password = Annotated[str, StringConstraints(min_length=8, max_length=256)]


class InvitationCreateRequest(BaseModel):
    """Create one invitation for an email address."""

    model_config = ConfigDict(extra="forbid")

    email: Email


class InvitationData(BaseModel):
    """Invitation response; token is returned only at creation time."""

    model_config = ConfigDict(extra="forbid")

    id: int
    email: str
    token: str
    status: InvitationStatus
    expires_at: datetime


class RegisterRequest(BaseModel):
    """Complete an invitation and create the active user account."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    invite_token: str = Field(
        min_length=16,
        max_length=256,
        validation_alias=AliasChoices("invite_token", "token", "invitation_token", "invite_code"),
    )
    username: Username
    password: Password
    email: Email | None = None


class LoginRequest(BaseModel):
    """Credentials accepted by the login endpoint."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class UserData(BaseModel):
    """Safe user projection; password hashes never cross this boundary."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: int
    username: str
    email: str
    role: UserRole
    status: UserStatus


class TokenData(BaseModel):
    """Access-token response data; refresh token is cookie-only."""

    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserData


class LogoutData(BaseModel):
    """Idempotent logout response."""

    model_config = ConfigDict(extra="forbid")

    logged_out: bool = True
