from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import and_, extract
from sqlalchemy.orm import Session, joinedload

from app.models import Category, Expense, Tracker, TrackerMember, User


Money = Decimal
CENT = Decimal("0.01")


def money(value: Decimal | int | float | str) -> Money:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def serialize_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "default_currency": user.default_currency,
        "is_admin": user.is_admin,
    }


def serialize_tracker(tracker: Tracker) -> dict[str, Any]:
    return {
        "id": tracker.id,
        "name": tracker.name,
        "default_currency": tracker.default_currency,
        "members": [serialize_member(member) for member in tracker.members],
    }


def serialize_member(member: TrackerMember) -> dict[str, Any]:
    return {
        "id": member.id,
        "user_id": member.user_id,
        "name": member.user.name,
        "email": member.user.email,
        "role": member.role,
        "share_percent": float(member.share_percent),
    }


def serialize_category(category: Category) -> dict[str, Any]:
    return {
        "id": category.id,
        "tracker_id": category.tracker_id,
        "name": category.name,
        "color": category.color,
    }


def serialize_expense(expense: Expense) -> dict[str, Any]:
    return {
        "id": expense.id,
        "tracker_id": expense.tracker_id,
        "category_id": expense.category_id,
        "category": expense.category.name,
        "category_color": expense.category.color,
        "paid_by_id": expense.paid_by_id,
        "paid_by": expense.paid_by.name,
        "date": expense.date.isoformat(),
        "amount": float(expense.amount),
        "currency": expense.currency,
        "description": expense.description,
        "is_shared": expense.is_shared,
    }


def get_tracker_for_user(session: Session, tracker_id: int, user: User) -> Tracker | None:
    query = (
        session.query(Tracker)
        .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
        .filter(Tracker.id == tracker_id)
    )
    if not user.is_admin:
        query = query.join(TrackerMember).filter(TrackerMember.user_id == user.id)
    return query.one_or_none()


def expense_query(session: Session, tracker_id: int, month: str | None = None, year: int | None = None):
    query = (
        session.query(Expense)
        .options(joinedload(Expense.category), joinedload(Expense.paid_by))
        .filter(Expense.tracker_id == tracker_id)
        .order_by(Expense.date.desc(), Expense.id.desc())
    )
    if month:
        year_part, month_part = month.split("-", 1)
        query = query.filter(
            and_(
                extract("year", Expense.date) == int(year_part),
                extract("month", Expense.date) == int(month_part),
            )
        )
    elif year:
        query = query.filter(extract("year", Expense.date) == year)
    return query


def overview_for_expenses(expenses: list[Expense]) -> dict[str, Any]:
    total = money("0")
    by_category: dict[str, Money] = defaultdict(lambda: money("0"))
    by_person: dict[str, dict[str, Money]] = defaultdict(lambda: {"shared": money("0"), "individual": money("0"), "total": money("0")})
    by_person_category: dict[str, dict[str, Money]] = defaultdict(lambda: defaultdict(lambda: money("0")))

    for expense in expenses:
        amount = money(expense.amount)
        total += amount
        by_category[expense.category.name] += amount
        person_totals = by_person[expense.paid_by.name]
        key = "shared" if expense.is_shared else "individual"
        person_totals[key] += amount
        person_totals["total"] += amount
        by_person_category[expense.paid_by.name][expense.category.name] += amount

    return {
        "total": float(total),
        "by_category": [{"name": name, "total": float(value)} for name, value in sorted(by_category.items())],
        "by_person": [
            {"name": name, "shared": float(values["shared"]), "individual": float(values["individual"]), "total": float(values["total"])}
            for name, values in sorted(by_person.items())
        ],
        "by_person_category": [
            {"person": person, "category": category, "total": float(value)}
            for person, categories in sorted(by_person_category.items())
            for category, value in sorted(categories.items())
        ],
    }


def normalized_shares(members: list[TrackerMember]) -> dict[int, Decimal]:
    if not members:
        return {}
    explicit_total = sum((Decimal(member.share_percent) for member in members), Decimal("0"))
    if explicit_total > 0:
        return {member.user_id: Decimal(member.share_percent) / explicit_total for member in members}
    equal = Decimal("1") / Decimal(len(members))
    return {member.user_id: equal for member in members}


def balance_for_tracker(tracker: Tracker, expenses: list[Expense]) -> dict[str, Any]:
    shares = normalized_shares(tracker.members)
    shared_expenses = [expense for expense in expenses if expense.is_shared]
    shared_total = sum((money(expense.amount) for expense in shared_expenses), money("0"))
    paid_shared: dict[int, Money] = defaultdict(lambda: money("0"))
    paid_individual: dict[int, Money] = defaultdict(lambda: money("0"))

    for expense in expenses:
        if expense.is_shared:
            paid_shared[expense.paid_by_id] += money(expense.amount)
        else:
            paid_individual[expense.paid_by_id] += money(expense.amount)

    rows = []
    net_by_user: dict[int, Money] = {}
    for member in tracker.members:
        expected = money(shared_total * shares.get(member.user_id, Decimal("0")))
        paid = money(paid_shared[member.user_id])
        net = money(paid - expected)
        net_by_user[member.user_id] = net
        rows.append(
            {
                "user_id": member.user_id,
                "name": member.user.name,
                "share_percent": float(shares.get(member.user_id, Decimal("0")) * Decimal("100")),
                "expected_shared": float(expected),
                "paid_shared": float(paid),
                "paid_individual": float(paid_individual[member.user_id]),
                "net": float(net),
            }
        )

    debtors = [{"user_id": user_id, "amount": -value} for user_id, value in net_by_user.items() if value < 0]
    creditors = [{"user_id": user_id, "amount": value} for user_id, value in net_by_user.items() if value > 0]
    settlements = []
    name_by_user = {member.user_id: member.user.name for member in tracker.members}
    debtor_index = 0
    creditor_index = 0
    while debtor_index < len(debtors) and creditor_index < len(creditors):
        debtor = debtors[debtor_index]
        creditor = creditors[creditor_index]
        amount = min(debtor["amount"], creditor["amount"]).quantize(CENT)
        if amount > 0:
            settlements.append(
                {
                    "from_user_id": debtor["user_id"],
                    "from": name_by_user[debtor["user_id"]],
                    "to_user_id": creditor["user_id"],
                    "to": name_by_user[creditor["user_id"]],
                    "amount": float(amount),
                }
            )
        debtor["amount"] -= amount
        creditor["amount"] -= amount
        if debtor["amount"] <= 0:
            debtor_index += 1
        if creditor["amount"] <= 0:
            creditor_index += 1

    return {
        "shared_total": float(shared_total),
        "rows": rows,
        "settlements": settlements,
    }


def current_year() -> int:
    return date.today().year
