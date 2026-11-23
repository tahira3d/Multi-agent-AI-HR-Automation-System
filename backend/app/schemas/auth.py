from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def email_format(cls, value: str) -> str:
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address")
        return value


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    role: str


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=32, max_length=256)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120, validation_alias=AliasChoices("name", "full_name"))
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=200)
    role: str = Field(pattern=r"^(recruiter|interviewer|admin)$")
    _email = field_validator("email")(classmethod(lambda cls, v: LoginRequest.email_format(v)))

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if len(value.strip()) < 2:
            raise ValueError("Full name is required")
        return value.strip()


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: UUID
    name: str
    full_name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime
