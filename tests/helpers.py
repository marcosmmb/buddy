from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import Category, Expense, Tracker, TrackerMember, User


def make_user(user_id: int, name: str, email: str | None = None, is_admin: bool = False) -> User:
    return User(
        id=user_id,
        email=email or f"{name.lower()}@example.test",
        name=name,
        password_hash="unused",
        default_currency="CAD",
        theme="light",
        is_admin=is_admin,
        is_active=True,
    )


def make_member(user: User, share_percent: str | int = "0", role: str = "member") -> TrackerMember:
    return TrackerMember(
        id=user.id,
        tracker_id=1,
        user_id=user.id,
        role=role,
        share_percent=Decimal(str(share_percent)),
        user=user,
    )


def make_tracker(*members: TrackerMember, tracker_id: int = 1, currency: str = "CAD") -> Tracker:
    tracker = Tracker(id=tracker_id, name="Home", default_currency=currency, created_by_id=members[0].user_id if members else 1)
    tracker.members = list(members)
    for member in tracker.members:
        member.tracker = tracker
        member.tracker_id = tracker.id
    return tracker


def make_category(category_id: int = 1, name: str = "Groceries", color: str = "#f1b84b", tracker_id: int = 1) -> Category:
    return Category(id=category_id, tracker_id=tracker_id, name=name, color=color)


def make_expense(
    amount: str,
    paid_by: User,
    category: Category,
    *,
    expense_id: int = 1,
    spent_on: date = date(2026, 7, 11),
    shared: bool = True,
    description: str = "Expense",
    currency: str = "CAD",
    tracker: Tracker | None = None,
) -> Expense:
    expense = Expense(
        id=expense_id,
        tracker_id=tracker.id if tracker is not None else category.tracker_id,
        category_id=category.id,
        paid_by_id=paid_by.id,
        date=spent_on,
        amount=Decimal(amount),
        currency=currency,
        description=description,
        is_shared=shared,
    )
    expense.category = category
    expense.paid_by = paid_by
    if tracker is not None:
        expense.tracker = tracker
    return expense
