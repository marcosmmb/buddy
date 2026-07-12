from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from litestar.exceptions import HTTPException
from sqlalchemy.orm import Session, joinedload

from app.models import (
    BankAccount,
    BankConnection,
    BankTransaction,
    Category,
    CsvImportConfig,
    Expense,
    Tracker,
    TrackerMember,
    TrackerMonthlyShare,
    User,
)
from app.services import SUPPORTED_CURRENCIES


BACKUP_SCHEMA_VERSION = 1


def decimal_text(value: Decimal | int | float | str) -> str:
    return str(Decimal(str(value)))


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid backup date: {value}") from exc


def load_tracker_for_backup(session: Session, tracker_id: int) -> Tracker:
    tracker = (
        session.query(Tracker)
        .options(
            joinedload(Tracker.members).joinedload(TrackerMember.user),
            joinedload(Tracker.categories),
            joinedload(Tracker.monthly_shares).joinedload(TrackerMonthlyShare.user),
            joinedload(Tracker.csv_configs).joinedload(CsvImportConfig.created_by),
        )
        .filter(Tracker.id == tracker_id)
        .one_or_none()
    )
    if tracker is None:
        raise HTTPException(status_code=404, detail="Tracker not found")
    return tracker


def export_tracker_backup(session: Session, tracker_id: int) -> dict[str, Any]:
    tracker = load_tracker_for_backup(session, tracker_id)
    created_by = session.get(User, tracker.created_by_id)
    expenses = (
        session.query(Expense)
        .options(joinedload(Expense.category), joinedload(Expense.paid_by))
        .filter(Expense.tracker_id == tracker_id)
        .order_by(Expense.date, Expense.id)
        .all()
    )
    return {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "tracker": {
            "id": tracker.id,
            "name": tracker.name,
            "default_currency": tracker.default_currency,
            "created_by_email": created_by.email if created_by is not None else None,
        },
        "members": [
            {
                "id": member.user_id,
                "email": member.user.email,
                "name": member.user.name,
                "role": member.role,
                "share_percent": decimal_text(member.share_percent),
            }
            for member in sorted(tracker.members, key=lambda item: item.user.email)
        ],
        "monthly_shares": [
            {
                "user_id": share.user_id,
                "email": share.user.email,
                "month": share.month,
                "share_percent": decimal_text(share.share_percent),
            }
            for share in sorted(tracker.monthly_shares, key=lambda item: (item.month, item.user.email))
        ],
        "categories": [
            {
                "id": category.id,
                "name": category.name,
                "color": category.color,
            }
            for category in sorted(tracker.categories, key=lambda item: item.name)
        ],
        "expenses": [
            {
                "id": expense.id,
                "category_id": expense.category_id,
                "category": expense.category.name,
                "paid_by_id": expense.paid_by_id,
                "paid_by_email": expense.paid_by.email,
                "date": expense.date.isoformat(),
                "amount": decimal_text(expense.amount),
                "currency": expense.currency,
                "description": expense.description,
                "is_shared": expense.is_shared,
            }
            for expense in expenses
        ],
        "csv_configs": [
            {
                "id": config.id,
                "name": config.name,
                "field_map": config.field_map or {},
                "invert_amount": config.invert_amount,
                "currency": config.currency,
                "created_by_email": config.created_by.email if config.created_by is not None else None,
            }
            for config in sorted(tracker.csv_configs, key=lambda item: item.name)
        ],
        "excluded": {
            "bank_connections": "Plaid access tokens and synced bank metadata are not included. Reconnect banks after restore.",
        },
    }


def restore_tracker_backup(session: Session, tracker_id: int, data: dict[str, Any], restored_by: User) -> dict[str, Any]:
    if int(data.get("schema_version", 0)) != BACKUP_SCHEMA_VERSION:
        raise HTTPException(status_code=400, detail="Unsupported backup schema version")
    tracker_payload = data.get("tracker") or {}
    tracker = session.get(Tracker, tracker_id)
    if tracker is None:
        raise HTTPException(status_code=404, detail="Tracker not found")

    user_by_old_id, user_by_email = users_for_backup(session, data.get("members") or [], restored_by)
    category_rows = data.get("categories") or []
    if not category_rows:
        raise HTTPException(status_code=400, detail="Backup must include at least one category")

    owner_ids = {user_by_old_id[backup_int(member.get("id"), "member ID")].id for member in data.get("members") or [] if str(member.get("role", "member")) == "owner"}
    if not owner_ids:
        raise HTTPException(status_code=400, detail="Backup must include at least one tracker owner")
    validate_backup_payload(data, user_by_old_id, user_by_email)

    connection_ids = [row.id for row in session.query(BankConnection.id).filter(BankConnection.tracker_id == tracker_id)]
    account_ids = []
    if connection_ids:
        account_ids = [row.id for row in session.query(BankAccount.id).filter(BankAccount.bank_connection_id.in_(connection_ids))]
    if account_ids:
        session.query(BankTransaction).filter(BankTransaction.bank_account_id.in_(account_ids)).delete(synchronize_session=False)
        session.query(BankAccount).filter(BankAccount.id.in_(account_ids)).delete(synchronize_session=False)
    if connection_ids:
        session.query(BankConnection).filter(BankConnection.id.in_(connection_ids)).delete(synchronize_session=False)
    session.query(Expense).filter(Expense.tracker_id == tracker_id).delete(synchronize_session=False)
    session.query(TrackerMonthlyShare).filter(TrackerMonthlyShare.tracker_id == tracker_id).delete(synchronize_session=False)
    session.query(CsvImportConfig).filter(CsvImportConfig.tracker_id == tracker_id).delete(synchronize_session=False)
    session.query(TrackerMember).filter(TrackerMember.tracker_id == tracker_id).delete(synchronize_session=False)
    session.query(Category).filter(Category.tracker_id == tracker_id).delete(synchronize_session=False)
    session.flush()

    tracker.name = str(tracker_payload.get("name") or tracker.name).strip() or tracker.name
    tracker.default_currency = backup_currency(tracker_payload.get("default_currency") or tracker.default_currency)
    created_by_email = str(tracker_payload.get("created_by_email") or "").lower()
    tracker.created_by_id = user_by_email.get(created_by_email, restored_by).id

    for member in data.get("members") or []:
        old_user_id = backup_int(member.get("id"), "member ID")
        session.add(
            TrackerMember(
                tracker_id=tracker_id,
                user_id=user_by_old_id[old_user_id].id,
                role=str(member.get("role", "member")),
            share_percent=backup_decimal(member.get("share_percent", "0"), "member share percent"),
            )
        )

    category_by_old_id: dict[int, Category] = {}
    category_by_name: dict[str, Category] = {}
    for row in category_rows:
        category = Category(
            tracker_id=tracker_id,
            name=str(row.get("name") or "Category").strip(),
            color=str(row.get("color") or "#f1b84b"),
        )
        session.add(category)
        session.flush()
        if row.get("id") is not None:
            category_by_old_id[backup_int(row.get("id"), "category ID")] = category
        category_by_name[category.name.lower()] = category

    for share in data.get("monthly_shares") or []:
        old_user_id = backup_int(share.get("user_id"), "monthly share user ID")
        if old_user_id not in user_by_old_id:
            raise HTTPException(status_code=400, detail="Monthly share references a missing backup member")
        session.add(
            TrackerMonthlyShare(
                tracker_id=tracker_id,
                user_id=user_by_old_id[old_user_id].id,
                month=str(share["month"]),
                share_percent=backup_decimal(share.get("share_percent", "0"), "monthly share percent"),
            )
        )

    for row in data.get("expenses") or []:
        category_id = backup_int(row.get("category_id", 0), "expense category ID")
        category = category_by_old_id.get(category_id) or category_by_name.get(str(row.get("category", "")).lower())
        if category is None:
            raise HTTPException(status_code=400, detail="Expense references a missing backup category")
        old_user_id = backup_int(row.get("paid_by_id", 0), "expense payer ID")
        paid_by = user_by_old_id.get(old_user_id) or user_by_email.get(str(row.get("paid_by_email", "")).lower())
        if paid_by is None:
            raise HTTPException(status_code=400, detail="Expense references a missing backup member")
        session.add(
            Expense(
                tracker_id=tracker_id,
                category_id=category.id,
                paid_by_id=paid_by.id,
                date=parse_date(str(row["date"])),
                amount=backup_decimal(row.get("amount", "0"), "expense amount"),
                currency=tracker.default_currency,
                description=str(row.get("description") or ""),
                is_shared=bool(row.get("is_shared", True)),
            )
        )

    for config in data.get("csv_configs") or []:
        created_by = user_by_email.get(str(config.get("created_by_email") or "").lower(), restored_by)
        session.add(
            CsvImportConfig(
                tracker_id=tracker_id,
                name=str(config.get("name") or "CSV schema").strip(),
                field_map=dict(config.get("field_map") or {}),
                invert_amount=bool(config.get("invert_amount", False)),
                currency=tracker.default_currency,
                created_by_id=created_by.id,
            )
        )

    session.flush()
    return {
        "status": "ok",
        "members": len(data.get("members") or []),
        "categories": len(category_rows),
        "expenses": len(data.get("expenses") or []),
        "monthly_shares": len(data.get("monthly_shares") or []),
        "csv_configs": len(data.get("csv_configs") or []),
    }


def users_for_backup(session: Session, members: list[dict[str, Any]], restored_by: User) -> tuple[dict[int, User], dict[str, User]]:
    if not members:
        raise HTTPException(status_code=400, detail="Backup must include at least one member")
    old_ids = [backup_int(member.get("id"), "member ID") for member in members]
    if len(old_ids) != len(set(old_ids)):
        raise HTTPException(status_code=400, detail="Backup contains duplicate member IDs")
    emails = {str(member.get("email") or "").strip().lower() for member in members}
    if "" in emails:
        raise HTTPException(status_code=400, detail="Every backup member must include an email")
    if len(emails) != len(members):
        raise HTTPException(status_code=400, detail="Backup contains duplicate member emails")
    rows = session.query(User).filter(User.email.in_(emails)).all()
    user_by_email = {user.email.lower(): user for user in rows}
    missing = sorted(email for email in emails if email not in user_by_email)
    if missing:
        raise HTTPException(status_code=400, detail=f"Create these users before restoring: {', '.join(missing)}")
    user_by_old_id = {backup_int(member.get("id"), "member ID"): user_by_email[str(member["email"]).strip().lower()] for member in members}
    if restored_by.email.lower() not in user_by_email:
        user_by_email[restored_by.email.lower()] = restored_by
    return user_by_old_id, user_by_email


def validate_backup_payload(data: dict[str, Any], user_by_old_id: dict[int, User], user_by_email: dict[str, User]) -> None:
    backup_currency((data.get("tracker") or {}).get("default_currency", "USD"))
    category_rows = data.get("categories") or []
    category_names = [str(row.get("name") or "").strip().lower() for row in category_rows]
    if any(not name for name in category_names):
        raise HTTPException(status_code=400, detail="Every backup category must include a name")
    if len(category_names) != len(set(category_names)):
        raise HTTPException(status_code=400, detail="Backup contains duplicate category names")
    category_ids = [backup_int(row.get("id"), "category ID") for row in category_rows if row.get("id") is not None]
    if len(category_ids) != len(set(category_ids)):
        raise HTTPException(status_code=400, detail="Backup contains duplicate category IDs")
    category_id_set = set(category_ids)
    category_name_set = set(category_names)

    monthly_keys: set[tuple[int, str]] = set()
    for share in data.get("monthly_shares") or []:
        old_user_id = backup_int(share.get("user_id"), "monthly share user ID")
        if old_user_id not in user_by_old_id:
            raise HTTPException(status_code=400, detail="Monthly share references a missing backup member")
        month = str(share.get("month") or "")
        try:
            datetime.strptime(month, "%Y-%m")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Monthly share month must use YYYY-MM format")
        backup_decimal(share.get("share_percent", "0"), "monthly share percent")
        key = (old_user_id, month)
        if key in monthly_keys:
            raise HTTPException(status_code=400, detail="Backup contains duplicate monthly shares")
        monthly_keys.add(key)

    for row in data.get("expenses") or []:
        category_id = row.get("category_id")
        category_name = str(row.get("category", "")).strip().lower()
        if (category_id is None or backup_int(category_id, "expense category ID") not in category_id_set) and category_name not in category_name_set:
            raise HTTPException(status_code=400, detail="Expense references a missing backup category")
        old_user_id = backup_int(row.get("paid_by_id", 0), "expense payer ID")
        paid_by_email = str(row.get("paid_by_email", "")).strip().lower()
        if old_user_id not in user_by_old_id and paid_by_email not in user_by_email:
            raise HTTPException(status_code=400, detail="Expense references a missing backup member")
        parse_date(str(row["date"]))
        backup_decimal(row.get("amount", "0"), "expense amount")

    csv_names = [str(config.get("name") or "").strip().lower() for config in data.get("csv_configs") or []]
    if any(not name for name in csv_names):
        raise HTTPException(status_code=400, detail="Every CSV schema backup entry must include a name")
    if len(csv_names) != len(set(csv_names)):
        raise HTTPException(status_code=400, detail="Backup contains duplicate CSV schema names")
    for config in data.get("csv_configs") or []:
        if not isinstance(config.get("field_map") or {}, dict):
            raise HTTPException(status_code=400, detail="CSV schema field_map must be an object")


def backup_currency(value: Any) -> str:
    currency = str(value or "").strip().upper()[:3]
    if currency not in SUPPORTED_CURRENCIES:
        raise HTTPException(status_code=400, detail="Backup contains unsupported tracker currency")
    return currency


def backup_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Backup contains invalid {label}") from exc


def backup_decimal(value: Any, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Backup contains invalid {label}") from exc
