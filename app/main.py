from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from typing import Annotated, Any

from litestar import Litestar, Request, delete, get, post, put
from litestar.exceptions import HTTPException
from litestar.params import Body
from litestar.response import Response
from litestar.static_files.config import StaticFilesConfig
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.db import db_session, init_database
from app.models import Category, CsvImportConfig, Expense, SessionToken, Tracker, TrackerMember, TrackerMonthlyShare, User
from app.schemas import (
    AdminUserCreatePayload,
    CategoryCreatePayload,
    CsvImportConfigPayload,
    CsvImportPayload,
    CsvPreviewPayload,
    ExpenseBulkDeletePayload,
    ExpenseCreatePayload,
    LoginPayload,
    MemberUpdatePayload,
    MonthlySharesPayload,
    PreferencesPayload,
    RegisterPayload,
    TrackerCreatePayload,
    TrackerUpdatePayload,
)
from app.security import hash_password, new_token, verify_password
from app.services import (
    SUPPORTED_CURRENCIES,
    balance_for_tracker,
    expense_query,
    get_tracker_for_user,
    is_tracker_owner,
    member_breakdown_for_expenses,
    money,
    monthly_share_overrides,
    monthly_totals_for_year,
    overview_for_expenses,
    period_options,
    serialize_category,
    serialize_csv_config,
    serialize_expense,
    serialize_tracker,
    serialize_user,
)


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


@get("/")
def index() -> Response[str]:
    return Response(content=(FRONTEND_DIR / "index.html").read_text(), media_type="text/html")


@post("/api/auth/login")
def login(data: Annotated[LoginPayload, Body()]) -> dict[str, Any]:
    with db_session() as session:
        user = session.query(User).filter(User.email == data.email.strip().lower()).one_or_none()
        if user is None or not user.is_active or not verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        token = new_token()
        session.add(SessionToken(token=token, user_id=user.id))
        return {"token": token, "user": serialize_user(user)}


@post("/api/auth/register")
def register(data: Annotated[RegisterPayload, Body()]) -> dict[str, Any]:
    with db_session() as session:
        user = User(
            email=data.email,
            name=data.name.strip(),
            password_hash=hash_password(data.password),
            default_currency=normalize_currency(data.default_currency),
            is_admin=False,
            is_active=True,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
        token = new_token()
        session.add(SessionToken(token=token, user_id=user.id))
        return {"token": token, "user": serialize_user(user)}


@delete("/api/auth/logout", status_code=200)
def logout(request: Request) -> dict[str, str]:
    auth_header = request.headers.get("authorization", "")
    token = auth_header.split(" ", 1)[1].strip() if " " in auth_header else ""
    with db_session() as session:
        session.query(SessionToken).filter(SessionToken.token == token).delete()
    return {"status": "ok"}


@get("/api/me")
def me(request: Request) -> dict[str, Any]:
    return serialize_user(require_user(request))


@put("/api/me/preferences")
def update_preferences(request: Request, data: Annotated[PreferencesPayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        db_user = session.get(User, user.id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="User not found")
        if data.name is not None:
            db_user.name = data.name.strip()
        if data.default_currency is not None:
            db_user.default_currency = normalize_currency(data.default_currency)
        if data.theme is not None:
            if data.theme not in {"light", "dark"}:
                raise HTTPException(status_code=400, detail="Theme must be light or dark")
            db_user.theme = data.theme
        if data.new_password:
            if not data.current_password or not verify_password(data.current_password, db_user.password_hash):
                raise HTTPException(status_code=400, detail="Current password is incorrect")
            db_user.password_hash = hash_password(data.new_password)
        session.flush()
        return serialize_user(db_user)


@get("/api/currencies")
def currencies() -> list[str]:
    return SUPPORTED_CURRENCIES


@get("/api/users")
def users(request: Request) -> list[dict[str, Any]]:
    require_user(request)
    with db_session() as session:
        return [serialize_user(user) for user in session.query(User).filter(User.is_active.is_(True)).order_by(User.name).all()]


@post("/api/admin/users")
def admin_create_user(request: Request, data: Annotated[AdminUserCreatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    require_admin(user)
    with db_session() as session:
        new_user = User(
            email=data.email,
            name=data.name.strip(),
            password_hash=hash_password(data.password),
            default_currency=normalize_currency(data.default_currency),
            is_admin=data.is_admin,
            is_active=True,
        )
        session.add(new_user)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
        return serialize_user(new_user)


@delete("/api/admin/users/{user_id:int}", status_code=200)
def admin_delete_user(request: Request, user_id: int) -> dict[str, str]:
    user = require_user(request)
    require_admin(user)
    if user.id == user_id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    with db_session() as session:
        db_user = session.get(User, user_id)
        if db_user is None or not db_user.is_active:
            raise HTTPException(status_code=404, detail="User not found")
        db_user.is_active = False
        session.query(SessionToken).filter(SessionToken.user_id == user_id).delete()
        session.query(TrackerMember).filter(TrackerMember.user_id == user_id).delete()
    return {"status": "ok"}


@get("/api/trackers")
def trackers(request: Request) -> list[dict[str, Any]]:
    user = require_user(request)
    with db_session() as session:
        query = session.query(Tracker).options(joinedload(Tracker.members).joinedload(TrackerMember.user))
        if not user.is_admin:
            query = query.join(TrackerMember).filter(TrackerMember.user_id == user.id)
        return [serialize_tracker(tracker) for tracker in query.order_by(Tracker.name).all()]


@post("/api/trackers")
def create_tracker(request: Request, data: Annotated[TrackerCreatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    require_admin(user)
    with db_session() as session:
        member_ids = sorted(set([user.id, *data.member_ids]))
        members = session.query(User).filter(User.id.in_(member_ids)).all()
        if len(members) != len(member_ids):
            raise HTTPException(status_code=400, detail="One or more members do not exist")
        tracker = Tracker(
            name=data.name.strip(),
            default_currency=normalize_currency(data.default_currency),
            created_by_id=user.id,
        )
        session.add(tracker)
        session.flush()
        share = Decimal("100") / Decimal(len(members)) if members else Decimal("0")
        for member in members:
            session.add(
                TrackerMember(
                    tracker_id=tracker.id,
                    user_id=member.id,
                    role="owner" if member.id == user.id else "member",
                    share_percent=share,
                )
            )
        for name, color in STARTER_CATEGORIES:
            session.add(Category(tracker_id=tracker.id, name=name, color=color))
        session.flush()
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker.id)
            .one()
        )
        return serialize_tracker(tracker)


@put("/api/trackers/{tracker_id:int}")
def update_tracker(request: Request, tracker_id: int, data: Annotated[TrackerUpdatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker_id)
            .one_or_none()
        )
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        if not is_tracker_owner(tracker, user):
            raise HTTPException(status_code=403, detail="Only tracker owners can update tracker settings")
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Tracker name is required")
        currency = normalize_currency(data.default_currency)
        tracker.name = name
        tracker.default_currency = currency
        session.query(Expense).filter(Expense.tracker_id == tracker_id).update({"currency": currency}, synchronize_session=False)
        session.query(CsvImportConfig).filter(CsvImportConfig.tracker_id == tracker_id).update({"currency": currency}, synchronize_session=False)
        session.flush()
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker_id)
            .one()
        )
        return serialize_tracker(tracker)


@delete("/api/trackers/{tracker_id:int}", status_code=200)
def delete_tracker(request: Request, tracker_id: int) -> dict[str, str]:
    user = require_user(request)
    with db_session() as session:
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker_id)
            .one_or_none()
        )
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        if not is_tracker_owner(tracker, user):
            raise HTTPException(status_code=403, detail="Only tracker owners can delete trackers")
        session.delete(tracker)
    return {"status": "ok"}


@put("/api/trackers/{tracker_id:int}/members")
def update_members(request: Request, tracker_id: int, data: Annotated[MemberUpdatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker_id)
            .one_or_none()
        )
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        if not is_tracker_owner(tracker, user):
            raise HTTPException(status_code=403, detail="Only tracker owners can manage members")
        validate_share_total(data.members)
        payload_by_user: dict[int, dict[str, Any]] = {}
        for item in data.members:
            user_id = int(item["user_id"])
            payload_by_user[user_id] = item
        if not payload_by_user:
            raise HTTPException(status_code=400, detail="A tracker needs at least one member")
        existing_users = session.query(User).filter(User.id.in_(payload_by_user.keys())).all()
        if len(existing_users) != len(payload_by_user):
            raise HTTPException(status_code=400, detail="One or more members do not exist")
        if not any(str(item.get("role", "member")) == "owner" for item in payload_by_user.values()):
            raise HTTPException(status_code=400, detail="A tracker needs at least one owner")
        session.query(TrackerMember).filter(TrackerMember.tracker_id == tracker_id).delete()
        for user_id, item in payload_by_user.items():
            session.add(
                TrackerMember(
                    tracker_id=tracker_id,
                    user_id=user_id,
                    role=str(item.get("role", "member")),
                    share_percent=Decimal(str(item.get("share_percent", 0))),
                )
            )
        session.flush()
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker_id)
            .one()
        )
        return serialize_tracker(tracker)


@get("/api/trackers/{tracker_id:int}/monthly-shares")
def monthly_shares(request: Request, tracker_id: int, month: str) -> dict[str, Any]:
    user = require_user(request)
    selected_month = normalize_month(month)
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        return monthly_share_response(session, tracker, selected_month)


@put("/api/trackers/{tracker_id:int}/monthly-shares")
def update_monthly_shares(request: Request, tracker_id: int, data: Annotated[MonthlySharesPayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    selected_month = normalize_month(data.month)
    validate_share_total(data.shares)
    with db_session() as session:
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker_id)
            .one_or_none()
        )
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        if not is_tracker_owner(tracker, user):
            raise HTTPException(status_code=403, detail="Only tracker owners can manage monthly shares")
        member_ids = {member.user_id for member in tracker.members}
        for item in data.shares:
            user_id = int(item["user_id"])
            if user_id not in member_ids:
                raise HTTPException(status_code=400, detail="Monthly shares can only be set for tracker members")
            share = (
                session.query(TrackerMonthlyShare)
                .filter(
                    TrackerMonthlyShare.tracker_id == tracker_id,
                    TrackerMonthlyShare.user_id == user_id,
                    TrackerMonthlyShare.month == selected_month,
                )
                .one_or_none()
            )
            if share is None:
                share = TrackerMonthlyShare(tracker_id=tracker_id, user_id=user_id, month=selected_month)
                session.add(share)
            share.share_percent = Decimal(str(item.get("share_percent", 0)))
        session.flush()
        return monthly_share_response(session, tracker, selected_month)


@get("/api/trackers/{tracker_id:int}/categories")
def categories(request: Request, tracker_id: int) -> list[dict[str, Any]]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        categories = session.query(Category).filter(Category.tracker_id == tracker_id).order_by(Category.name).all()
        return [serialize_category(category) for category in categories]


@post("/api/trackers/{tracker_id:int}/categories")
def create_category(request: Request, tracker_id: int, data: Annotated[CategoryCreatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        category = Category(tracker_id=tracker_id, name=data.name.strip(), color=data.color)
        session.add(category)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="That category already exists") from exc
        return serialize_category(category)


@delete("/api/trackers/{tracker_id:int}/categories/{category_id:int}", status_code=200)
def delete_category(request: Request, tracker_id: int, category_id: int) -> dict[str, str]:
    user = require_user(request)
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        if not is_tracker_owner(tracker, user):
            raise HTTPException(status_code=403, detail="Only tracker owners can manage categories")
        has_expenses = session.query(Expense).filter(Expense.category_id == category_id).first() is not None
        if has_expenses:
            raise HTTPException(status_code=400, detail="Cannot delete a category that has expenses")
        deleted = session.query(Category).filter(Category.id == category_id, Category.tracker_id == tracker_id).delete()
        if not deleted:
            raise HTTPException(status_code=404, detail="Category not found")
    return {"status": "ok"}


@get("/api/trackers/{tracker_id:int}/expenses")
def expenses(request: Request, tracker_id: int, month: str | None = None, year: int | None = None) -> list[dict[str, Any]]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        rows = expense_query(session, tracker_id, month=month, year=year).all()
        return [serialize_expense(expense) for expense in rows]


@post("/api/trackers/{tracker_id:int}/expenses")
def create_expense(request: Request, tracker_id: int, data: Annotated[ExpenseCreatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = load_tracker_member_context(session, tracker_id, user)
        validate_expense_payload(session, tracker, tracker_id, data)
        expense = Expense(
            tracker_id=tracker_id,
            category_id=data.category_id,
            paid_by_id=data.paid_by_id,
            date=data.date,
            amount=data.amount,
            currency=tracker.default_currency,
            description=data.description.strip(),
            is_shared=data.is_shared,
        )
        session.add(expense)
        session.flush()
        expense = (
            session.query(Expense)
            .options(joinedload(Expense.category), joinedload(Expense.paid_by), joinedload(Expense.tracker))
            .filter(Expense.id == expense.id)
            .one()
        )
        return serialize_expense(expense)


@put("/api/trackers/{tracker_id:int}/expenses/{expense_id:int}")
def update_expense(request: Request, tracker_id: int, expense_id: int, data: Annotated[ExpenseCreatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = load_tracker_member_context(session, tracker_id, user)
        validate_expense_payload(session, tracker, tracker_id, data)
        expense = session.query(Expense).filter(Expense.id == expense_id, Expense.tracker_id == tracker_id).one_or_none()
        if expense is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        expense.category_id = data.category_id
        expense.paid_by_id = data.paid_by_id
        expense.date = data.date
        expense.amount = data.amount
        expense.currency = tracker.default_currency
        expense.description = data.description.strip()
        expense.is_shared = data.is_shared
        session.flush()
        expense = (
            session.query(Expense)
            .options(joinedload(Expense.category), joinedload(Expense.paid_by), joinedload(Expense.tracker))
            .filter(Expense.id == expense.id)
            .one()
        )
        return serialize_expense(expense)


@delete("/api/trackers/{tracker_id:int}/expenses/{expense_id:int}", status_code=200)
def delete_expense(request: Request, tracker_id: int, expense_id: int) -> dict[str, str]:
    user = require_user(request)
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        deleted = session.query(Expense).filter(Expense.id == expense_id, Expense.tracker_id == tracker_id).delete()
        if not deleted:
            raise HTTPException(status_code=404, detail="Expense not found")
    return {"status": "ok"}


@post("/api/trackers/{tracker_id:int}/expenses/bulk-delete", status_code=200)
def bulk_delete_expenses(request: Request, tracker_id: int, data: Annotated[ExpenseBulkDeletePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        if not data.expense_ids:
            return {"deleted": 0}
        deleted = (
            session.query(Expense)
            .filter(Expense.tracker_id == tracker_id, Expense.id.in_(data.expense_ids))
            .delete(synchronize_session=False)
        )
    return {"deleted": deleted}


@get("/api/trackers/{tracker_id:int}/period-options")
def tracker_period_options(request: Request, tracker_id: int) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        return period_options(session, tracker_id)


@get("/api/trackers/{tracker_id:int}/overview")
def overview(
    request: Request,
    tracker_id: int,
    period_type: str = "month",
    period: str | None = None,
    month: str | None = None,
    year: int | None = None,
) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        if month:
            period_type = "month"
            period = month
        elif year:
            period_type = "year"
            period = str(year)
        elif period is None:
            period = date.today().strftime("%Y-%m")
        if period_type == "year":
            selected_year = int(period or date.today().year)
            rows = expense_query(session, tracker_id, year=selected_year).all()
            return {
                "period_type": "year",
                "period": str(selected_year),
                "summary": overview_for_expenses(rows),
                "member_breakdown": member_breakdown_for_expenses(session, tracker, rows),
                "monthly_totals": monthly_totals_for_year(session, tracker_id, selected_year),
                "expenses": [serialize_expense(expense) for expense in rows],
            }
        rows = expense_query(session, tracker_id, month=period).all()
        share_overrides = monthly_share_overrides(session, tracker_id, period)
        return {
            "period_type": "month",
            "period": period,
            "summary": overview_for_expenses(rows),
            "member_breakdown": member_breakdown_for_expenses(session, tracker, rows),
            "balance": balance_for_tracker(tracker, rows, share_overrides),
            "monthly_totals": [],
            "expenses": [serialize_expense(expense) for expense in rows],
        }


@get("/api/trackers/{tracker_id:int}/csv-configs")
def csv_configs(request: Request, tracker_id: int) -> list[dict[str, Any]]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        configs = session.query(CsvImportConfig).filter(CsvImportConfig.tracker_id == tracker_id).order_by(CsvImportConfig.name).all()
        return [serialize_csv_config(config) for config in configs]


@post("/api/trackers/{tracker_id:int}/csv-configs")
def create_csv_config(request: Request, tracker_id: int, data: Annotated[CsvImportConfigPayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    require_admin(user)
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        field_map = {key: clean_cell(value) for key, value in data.field_map.items() if clean_cell(value)}
        config = CsvImportConfig(
            tracker_id=tracker_id,
            name=data.name.strip(),
            field_map=field_map,
            invert_amount=data.invert_amount,
            currency=tracker.default_currency,
            created_by_id=user.id,
        )
        session.add(config)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A CSV config with that name already exists") from exc
        return serialize_csv_config(config)


@delete("/api/trackers/{tracker_id:int}/csv-configs/{config_id:int}", status_code=200)
def delete_csv_config(request: Request, tracker_id: int, config_id: int) -> dict[str, str]:
    user = require_user(request)
    require_admin(user)
    with db_session() as session:
        deleted = session.query(CsvImportConfig).filter(CsvImportConfig.id == config_id, CsvImportConfig.tracker_id == tracker_id).delete()
        if not deleted:
            raise HTTPException(status_code=404, detail="CSV config not found")
    return {"status": "ok"}


@post("/api/trackers/{tracker_id:int}/csv-imports/preview", status_code=200)
def preview_csv_import(request: Request, tracker_id: int, data: Annotated[CsvPreviewPayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = load_tracker_member_context(session, tracker_id, user)
        config = session.get(CsvImportConfig, data.config_id)
        if config is None or config.tracker_id != tracker_id:
            raise HTTPException(status_code=404, detail="CSV config not found")
        return build_csv_preview_rows(session, tracker, tracker_id, config, data)


@post("/api/trackers/{tracker_id:int}/csv-imports")
def import_csv(request: Request, tracker_id: int, data: Annotated[CsvImportPayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = load_tracker_member_context(session, tracker_id, user)
        imported = 0
        skipped: list[dict[str, Any]] = []
        for index, row in enumerate(data.expenses, start=1):
            try:
                validate_expense_payload(session, tracker, tracker_id, row)
                session.add(
                    Expense(
                        tracker_id=tracker_id,
                        category_id=row.category_id,
                        paid_by_id=row.paid_by_id,
                        date=row.date,
                        amount=row.amount,
                        currency=tracker.default_currency,
                        description=row.description.strip(),
                        is_shared=row.is_shared,
                    )
                )
                imported += 1
            except Exception as exc:
                skipped.append({"row": index, "reason": str(exc)})
        session.flush()
        return {"imported": imported, "skipped": skipped}


@get("/api/trackers/{tracker_id:int}/csv-exports")
def export_csv(request: Request, tracker_id: int, config_id: int, month: str | None = None) -> Response[str]:
    user = require_user(request)
    selected_month = normalize_month(month or date.today().strftime("%Y-%m"))
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        config = session.get(CsvImportConfig, config_id)
        if config is None or config.tracker_id != tracker_id:
            raise HTTPException(status_code=404, detail="CSV config not found")
        rows = expense_query(session, tracker_id, month=selected_month).all()
        filename = csv_export_filename(tracker, config, selected_month)
        return Response(
            content=build_csv_export(config, rows),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


app = Litestar(
    route_handlers=[
        index,
        login,
        logout,
        me,
        update_preferences,
        currencies,
        users,
        admin_create_user,
        admin_delete_user,
        trackers,
        create_tracker,
        update_tracker,
        delete_tracker,
        update_members,
        monthly_shares,
        update_monthly_shares,
        categories,
        create_category,
        delete_category,
        expenses,
        create_expense,
        update_expense,
        delete_expense,
        bulk_delete_expenses,
        tracker_period_options,
        overview,
        csv_configs,
        create_csv_config,
        delete_csv_config,
        preview_csv_import,
        import_csv,
        export_csv,
    ],
    on_startup=[init_database, normalize_legacy_category_colors],
    static_files_config=[
        StaticFilesConfig(path="/static", directories=[FRONTEND_DIR / "static"]),
    ],
)
