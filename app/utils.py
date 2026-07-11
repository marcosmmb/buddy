from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Any

from litestar import Request
from litestar.exceptions import HTTPException
from sqlalchemy.orm import joinedload

from app.db import db_session
from app.models import Category, CsvImportConfig, Expense, SessionToken, Tracker, TrackerMonthlyShare, User
from app.schemas import CsvPreviewPayload, ExpenseCreatePayload
from app.services import SUPPORTED_CURRENCIES, get_tracker_for_user, money


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
STARTER_CATEGORIES = [
    ("Groceries", "#f1b84b"),
    ("Restaurants", "#b45309"),
    ("Transportation", "#285c9d"),
    ("Housing", "#7c3aed"),
    ("Utilities", "#d99b25"),
    ("Entertainment", "#be123c"),
    ("Health", "#f4c45d"),
    ("Travel", "#0369a1"),
    ("Shopping", "#9333ea"),
    ("Other", "#6b7280"),
]
LEGACY_STARTER_CATEGORY_COLORS = {
    ("Groceries", "#166d5b"): "#f1b84b",
    ("Utilities", "#0f766e"): "#d99b25",
    ("Health", "#047857"): "#f4c45d",
}


def require_user(request: Request) -> User:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    with db_session() as session:
        session_token = (
            session.query(SessionToken)
            .options(joinedload(SessionToken.user))
            .filter(SessionToken.token == token)
            .one_or_none()
        )
        if session_token is None or not session_token.user.is_active:
            raise HTTPException(status_code=401, detail="Invalid session")
        session.expunge(session_token.user)
        return session_token.user


def require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def normalize_currency(value: str) -> str:
    value = value.strip().upper()
    if value not in SUPPORTED_CURRENCIES:
        raise HTTPException(status_code=400, detail="Unsupported currency")
    return value


def validate_share_total(members: list[dict[str, Any]]) -> None:
    total = sum((Decimal(str(item.get("share_percent", 0))) for item in members), Decimal("0"))
    if total > Decimal("100"):
        raise HTTPException(status_code=400, detail="Member share percentages cannot exceed 100%")


def normalize_month(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Month must use YYYY-MM format") from exc
    return value


def clean_cell(value: Any) -> str:
    return str(value or "").strip().strip('"').strip()


def parse_csv_date(value: str) -> date:
    value = clean_cell(value)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d", "%b %d, %Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported date format: {value}")


def parse_amount(value: str, invert: bool) -> Decimal:
    cleaned = clean_cell(value).replace("$", "").replace(",", "")
    amount = Decimal(cleaned)
    return -amount if invert else amount


def load_tracker_member_context(session: Any, tracker_id: int, user: User) -> Tracker:
    tracker = get_tracker_for_user(session, tracker_id, user)
    if tracker is None:
        raise HTTPException(status_code=404, detail="Tracker not found")
    return tracker


def validate_expense_payload(session: Any, tracker: Tracker, tracker_id: int, data: ExpenseCreatePayload) -> None:
    member_ids = {member.user_id for member in tracker.members}
    if data.paid_by_id not in member_ids:
        raise HTTPException(status_code=400, detail="Payer must be a tracker member")
    category = session.get(Category, data.category_id)
    if category is None or category.tracker_id != tracker_id:
        raise HTTPException(status_code=400, detail="Category must belong to the tracker")


def build_csv_preview_rows(
    session: Any,
    tracker: Tracker,
    tracker_id: int,
    config: CsvImportConfig,
    data: CsvPreviewPayload,
) -> dict[str, Any]:
    fallback_category = session.get(Category, data.fallback_category_id)
    if fallback_category is None or fallback_category.tracker_id != tracker_id:
        raise HTTPException(status_code=400, detail="Fallback category must belong to the tracker")
    member_ids = {member.user_id for member in tracker.members}
    if data.fallback_paid_by_id not in member_ids:
        raise HTTPException(status_code=400, detail="Fallback payer must be a tracker member")

    reader = csv.DictReader(StringIO(data.csv_text.lstrip("\ufeff")))
    reader.fieldnames = [clean_cell(name).lstrip("\ufeff") for name in (reader.fieldnames or [])]
    category_by_name = {category.name.lower(): category for category in session.query(Category).filter(Category.tracker_id == tracker_id).all()}
    user_by_key = {}
    name_by_user = {}
    for member in tracker.members:
        user_by_key[member.user.name.lower()] = member.user_id
        user_by_key[member.user.email.lower()] = member.user_id
        name_by_user[member.user_id] = member.user.name

    rows = []
    skipped: list[dict[str, Any]] = []
    field_map = config.field_map or {}
    for index, row in enumerate(reader, start=2):
        cleaned_row = {clean_cell(key).lstrip("\ufeff"): clean_cell(value) for key, value in row.items()}
        try:
            if not field_map.get("date") or not field_map.get("amount"):
                raise ValueError("CSV config must map date and amount")
            expense_date = parse_csv_date(cleaned_row.get(field_map["date"], ""))
            amount = parse_amount(cleaned_row.get(field_map["amount"], ""), config.invert_amount)
            description = cleaned_row.get(field_map.get("description", ""), "")
            category = fallback_category
            if field_map.get("category"):
                category_name = cleaned_row.get(field_map["category"], "").lower()
                category = category_by_name.get(category_name, fallback_category)
            paid_by_id = data.fallback_paid_by_id
            if field_map.get("paid_by"):
                paid_by_id = user_by_key.get(cleaned_row.get(field_map["paid_by"], "").lower(), data.fallback_paid_by_id)
            rows.append(
                {
                    "row_number": index,
                    "date": expense_date.isoformat(),
                    "category_id": category.id,
                    "category": category.name,
                    "paid_by_id": paid_by_id,
                    "paid_by": name_by_user.get(paid_by_id, ""),
                    "amount": float(amount),
                    "currency": tracker.default_currency,
                    "description": description,
                    "is_shared": data.is_shared,
                }
            )
        except Exception as exc:
            skipped.append({"row": index, "reason": str(exc)})
    return {"rows": rows, "skipped": skipped}


def csv_export_value(expense: Expense, field: str, invert_amount: bool) -> str:
    if field == "date":
        return expense.date.isoformat()
    if field == "amount":
        amount = -expense.amount if invert_amount else expense.amount
        return f"{money(amount)}"
    if field == "description":
        return expense.description or ""
    if field == "category":
        return expense.category.name
    if field == "paid_by":
        return expense.paid_by.name
    if field == "is_shared":
        return "Shared" if expense.is_shared else "Individual"
    return ""


def build_csv_export(config: CsvImportConfig, expenses: list[Expense]) -> str:
    field_map = config.field_map or {}
    ordered_fields = ["date", "description", "amount", "category", "paid_by", "is_shared"]
    columns = [(field, clean_cell(field_map.get(field))) for field in ordered_fields if clean_cell(field_map.get(field))]
    if not columns:
        raise HTTPException(status_code=400, detail="CSV config must map at least one field to export")
    headers = [column for _, column in columns]
    if len(headers) != len(set(headers)):
        raise HTTPException(status_code=400, detail="CSV config cannot export duplicate column names")

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for expense in expenses:
        writer.writerow({column: csv_export_value(expense, field, config.invert_amount) for field, column in columns})
    return output.getvalue()


def csv_export_filename(tracker: Tracker, config: CsvImportConfig, month: str) -> str:
    raw = f"{tracker.name}-{config.name}-{month}.csv".lower()
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in raw)


def monthly_share_response(session: Any, tracker: Tracker, month: str) -> dict[str, Any]:
    overrides = {
        share.user_id: share
        for share in session.query(TrackerMonthlyShare).filter(
            TrackerMonthlyShare.tracker_id == tracker.id,
            TrackerMonthlyShare.month == month,
        )
    }
    return {
        "month": month,
        "shares": [
            {
                "user_id": member.user_id,
                "name": member.user.name,
                "email": member.user.email,
                "default_share_percent": float(member.share_percent),
                "share_percent": float(overrides.get(member.user_id, member).share_percent),
                "has_override": member.user_id in overrides,
            }
            for member in tracker.members
        ],
    }


def normalize_legacy_category_colors() -> None:
    with db_session() as session:
        for (name, old_color), new_color in LEGACY_STARTER_CATEGORY_COLORS.items():
            session.query(Category).filter(Category.name == name, Category.color == old_color).update(
                {"color": new_color},
                synchronize_session=False,
            )
