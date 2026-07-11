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
    is_admin: bool = False

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
    theme: str | None = None
    current_password: str | None = None
    new_password: str | None = Field(default=None, min_length=8)


class AdminUserCreatePayload(BaseModel):
    email: str
    name: str
    password: str = Field(min_length=8)
    default_currency: str = "USD"
    is_admin: bool = False

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


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


class ExpenseBulkDeletePayload(BaseModel):
    expense_ids: list[int]


class CsvImportConfigPayload(BaseModel):
    name: str
    field_map: dict[str, str | None] = Field(default_factory=dict)
    invert_amount: bool = False
    currency: str = "USD"


class CsvPreviewPayload(BaseModel):
    config_id: int
    csv_text: str
    fallback_category_id: int
    fallback_paid_by_id: int
    is_shared: bool = False


class CsvImportPayload(BaseModel):
    expenses: list[ExpenseCreatePayload]
