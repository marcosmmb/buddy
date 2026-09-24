"""Populate an existing Buddy deployment with a separate manual-testing tracker."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.config import settings
from app.db import db_session
from app.models import BankAccount, BankConnection, BankTransaction, Category, CsvImportConfig, Expense, Tracker, TrackerMember, User, utcnow


def seed_demo(session: Session, user_email: str, today: date | None = None) -> tuple[Tracker, bool]:
    """Create once per owner; never replace existing demo edits or real tracker data."""
    today = today or utcnow().date()
    owner = session.query(User).filter(User.email == user_email.strip().lower(), User.is_active.is_(True)).one_or_none()
    if owner is None:
        raise ValueError(f"Active user not found: {user_email}. Choose an existing account with --user-email.")
    marker = f"buddy-demo-v1-user-{owner.id}"
    existing = session.query(BankConnection).filter(BankConnection.provider_item_id == marker).one_or_none()
    if existing is not None:
        if existing.provider != "demo" or existing.user_id != owner.id or existing.tracker.created_by_id != owner.id:
            raise ValueError("Demo identifier is already in use by another connection.")
        return existing.tracker, False

    companion_email = f"buddy-demo-housemate-{owner.id}@example.invalid"
    companion = session.query(User).filter(User.email == companion_email).one_or_none()
    if companion is None:
        companion = User(
            email=companion_email,
            name="Demo Housemate",
            password_hash="disabled-demo-account",
            is_active=False,
            default_currency=owner.default_currency,
        )
        session.add(companion)
        session.flush()
    elif companion.is_active or companion.name != "Demo Housemate":
        raise ValueError("Demo housemate email is already in use by another account.")

    tracker = Tracker(name="Buddy Demo", default_currency=owner.default_currency, created_by_id=owner.id)
    session.add(tracker)
    session.flush()
    for user, role, share in [(owner, "owner", "60"), (companion, "member", "40")]:
        session.add(TrackerMember(tracker_id=tracker.id, user_id=user.id, role=role, share_percent=Decimal(share)))

    categories = {}
    for name, color in [("Groceries", "#56a36c"), ("Housing", "#617de8"), ("Dining", "#f1b84b"), ("Transport", "#c77bba"), ("Entertainment", "#e17860")]:
        category = Category(tracker_id=tracker.id, name=name, color=color)
        session.add(category)
        categories[name] = category
    session.flush()

    previous_month = today.replace(day=1) - timedelta(days=1)
    samples = [
        (today.replace(day=1), "Housing", "1600.00", owner, "Demo: monthly rent", True),
        (today, "Groceries", "128.40", owner, "Demo: weekly groceries", True),
        (today, "Dining", "64.50", companion, "Demo: dinner together", True),
        (today, "Entertainment", "29.99", owner, "Demo: personal subscription", False),
        (today, "Transport", "24.00", companion, "Demo: personal transit", False),
        (today, "Dining", "5.25", owner, "Demo: duplicate coffee", False),
        (today, "Dining", "5.25", owner, "Demo: duplicate coffee", False),
        (previous_month, "Housing", "1550.00", owner, "Demo: previous month rent", True),
        (previous_month, "Groceries", "96.80", companion, "Demo: previous month groceries", True),
        (previous_month, "Entertainment", "18.00", companion, "Demo: previous month cinema", False),
    ]
    for spent_on, category, amount, payer, description, shared in samples:
        session.add(Expense(
            tracker_id=tracker.id, category_id=categories[category].id, paid_by_id=payer.id,
            date=spent_on, amount=Decimal(amount), currency=tracker.default_currency,
            description=description, is_shared=shared,
        ))
    session.add(CsvImportConfig(
        tracker_id=tracker.id, name="Demo CSV", created_by_id=owner.id, currency=tracker.default_currency,
        field_map={"date": "Date", "description": "Description", "amount": "Amount"},
    ))

    connection = BankConnection(
        tracker_id=tracker.id, user_id=owner.id, provider="demo", provider_item_id=marker,
        institution_name="Demo Bank (sample data)", encrypted_access_token="", last_synced_at=utcnow(),
    )
    session.add(connection)
    session.flush()
    account = BankAccount(
        bank_connection_id=connection.id, provider_account_id=f"{marker}-checking",
        name="Demo checking", mask="0000", type="depository", subtype="checking", currency=tracker.default_currency,
    )
    session.add(account)
    session.flush()
    transactions = [
        (0, "Demo: supermarket", "73.42", False),
        (1, "Demo: coffee shop", "6.50", False),
        (1, "Demo: coffee shop", "6.50", False),
        (2, "Demo: electricity", "89.20", False),
        (3, "Demo: personal purchase (try Ignore)", "22.00", False),
        (4, "Demo: restaurant", "58.75", False),
        (5, "Demo: transit pass", "40.00", False),
        (6, "Demo: streaming subscription", "14.99", False),
        (0, "Demo: pending purchase (excluded from review)", "12.00", True),
        (0, "Demo: payroll (excluded from review)", "-2500.00", False),
    ]
    for index, (days_ago, name, amount, pending) in enumerate(transactions):
        session.add(BankTransaction(
            bank_account_id=account.id, provider_transaction_id=f"{marker}-transaction-{index}",
            date=today - timedelta(days=days_ago), name=name, merchant_name=name,
            amount=Decimal(amount), currency=tracker.default_currency, pending=pending,
            status="pending" if pending else "ready", raw_payload={"demo": True},
        ))
    session.flush()
    return tracker, True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", default=os.getenv("BUDDY_DEMO_USER") or settings.admin_email,
                        help="Existing user who will own the demo and see its bank review queue (default: ADMIN_EMAIL).")
    args = parser.parse_args()
    try:
        with db_session() as session:
            tracker, created = seed_demo(session, args.user_email)
            tracker_id, tracker_name = tracker.id, tracker.name
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    print(f"{'Created' if created else 'Kept existing'} tracker: {tracker_name} (id {tracker_id})")
    print(f"Sign in as: {args.user_email}")
    if created:
        print("Added 10 expenses across two months and 8 transactions to review.")
    else:
        print("Existing demo edits, imports, and ignored transactions were preserved.")
    print(f"Overview: http://localhost:3088/?tracker={tracker_id}&tab=overview (select {utcnow():%Y-%m})")
    print(f"Bank Import: http://localhost:3088/?tracker={tracker_id}&tab=bank")


if __name__ == "__main__":
    main()
