from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator


class LoginPayload(BaseModel):
    email: str
    password: str


class RegisterPayload(BaseModel):
    email: str
    name: str
    password: str = Field(min_length=8)
    default_currency: str = "USD"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("default_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()[:3]


class PreferencesPayload(BaseModel):
    name: str | None = None
    default_currency: str | None = None


class TrackerCreatePayload(BaseModel):
    name: str
    default_currency: str = "USD"
    member_ids: list[int] = Field(default_factory=list)


class MemberUpdatePayload(BaseModel):
    members: list[dict[str, Any]]


class CategoryCreatePayload(BaseModel):
    name: str
    color: str = "#4677ff"


class ExpenseCreatePayload(BaseModel):
    date: date
    category_id: int
    amount: Decimal
    currency: str
    paid_by_id: int
    description: str = ""
    is_shared: bool = True

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()[:3]
