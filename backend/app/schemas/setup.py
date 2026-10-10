"""Public first-run setup contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import Email, Password, UserData, Username


class SetupStatusData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["NOT_READY", "PENDING", "COMPLETED"]


class CompleteSetupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=77, max_length=77, pattern=r"^apanel_setup_[A-Za-z0-9_-]{64}$")
    username: Username
    email: Email
    password: Password


class CompleteSetupData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: UserData
