from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import and_, extract, func
from sqlalchemy.orm import Session, joinedload

from app.models import Category, CsvImportConfig, Expense, Tracker, TrackerMember, TrackerMonthlyShare, User


Money = Decimal
CENT = Decimal("0.01")
SUPPORTED_CURRENCIES = ["USD", "CAD", "EUR", "GBP", "MXN", "BRL", "ARS", "CLP", "AUD", "JPY"]


def money(value: Decimal | int | float | str) -> Money:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def serialize_user(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "default_currency": user.default_currency,
        "theme": user.theme,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
    }


def serialize_tracker(tracker: Tracker) -> dict[str, Any]:
    return {
        "id": tracker.id,
        "name": tracker.name,
        "default_currency": tracker.default_currency,
        "members": [serialize_member(member) for member in tracker.members],
        "owners": [member.user_id for member in tracker.members if member.role == "owner"],
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
    tracker_currency = expense.tracker.default_currency if expense.tracker is not None else expense.currency
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
        "currency": tracker_currency,
        "description": expense.description,
        "is_shared": expense.is_shared,
    }


def serialize_csv_config(config: CsvImportConfig) -> dict[str, Any]:
    return {
        "id": config.id,
        "tracker_id": config.tracker_id,
        "name": config.name,
        "field_map": config.field_map or {},
        "invert_amount": config.invert_amount,
        "currency": config.currency,
    }


def is_tracker_owner(tracker: Tracker, user: User) -> bool:
    return user.is_admin or any(member.user_id == user.id and member.role == "owner" for member in tracker.members)


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
        .options(joinedload(Expense.category), joinedload(Expense.paid_by), joinedload(Expense.tracker))
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
        amount = Decimal(expense.amount)
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


def member_breakdown_for_expenses(session: Session, tracker: Tracker, expenses: list[Expense]) -> list[dict[str, Any]]:
    rows: dict[int, dict[str, Any]] = {
        member.user_id: {
            "user_id": member.user_id,
            "name": member.user.name,
            "responsibility_shared": Decimal("0"),
            "responsibility_individual": Decimal("0"),
            "responsibility_total": Decimal("0"),
            "paid_shared": Decimal("0"),
            "paid_individual": Decimal("0"),
            "paid_total": Decimal("0"),
        }
        for member in tracker.members
    }
    shared_by_month: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for expense in expenses:
        amount = Decimal(expense.amount)
        row = rows.setdefault(
            expense.paid_by_id,
            {
                "user_id": expense.paid_by_id,
                "name": expense.paid_by.name,
                "responsibility_shared": Decimal("0"),
                "responsibility_individual": Decimal("0"),
                "responsibility_total": Decimal("0"),
                "paid_shared": Decimal("0"),
                "paid_individual": Decimal("0"),
                "paid_total": Decimal("0"),
            },
        )
        row["paid_total"] += amount
        if expense.is_shared:
            row["paid_shared"] += amount
            shared_by_month[expense.date.strftime("%Y-%m")] += amount
        else:
            row["paid_individual"] += amount
            row["responsibility_individual"] += amount

    for month, shared_total in shared_by_month.items():
        shares = normalized_shares(tracker.members, monthly_share_overrides(session, tracker.id, month))
        for user_id, share_ratio in shares.items():
            if user_id in rows:
                rows[user_id]["responsibility_shared"] += shared_total * share_ratio

    result = []
    for row in rows.values():
        row["responsibility_total"] = row["responsibility_shared"] + row["responsibility_individual"]
        if not row["responsibility_total"] and not row["paid_total"]:
            continue
        result.append(
            {
                "user_id": row["user_id"],
                "name": row["name"],
                "responsibility_shared": float(row["responsibility_shared"]),
                "responsibility_individual": float(row["responsibility_individual"]),
                "responsibility_total": float(row["responsibility_total"]),
                "paid_shared": float(row["paid_shared"]),
                "paid_individual": float(row["paid_individual"]),
                "paid_total": float(row["paid_total"]),
            }
        )
    return sorted(result, key=lambda item: item["name"])


def normalized_shares(members: list[TrackerMember], overrides: dict[int, Decimal] | None = None) -> dict[int, Decimal]:
    if not members:
        return {}
    overrides = overrides or {}
    share_by_user = {
        member.user_id: Decimal(overrides.get(member.user_id, member.share_percent))
        for member in members
    }
    explicit_total = sum(share_by_user.values(), Decimal("0"))
    if explicit_total > 0:
        return {user_id: share / Decimal("100") for user_id, share in share_by_user.items()}
    equal = Decimal("1") / Decimal(len(members))
    return {member.user_id: equal for member in members}


def monthly_share_overrides(session: Session, tracker_id: int, month: str) -> dict[int, Decimal]:
    return {
        share.user_id: Decimal(share.share_percent)
        for share in session.query(TrackerMonthlyShare).filter(
            TrackerMonthlyShare.tracker_id == tracker_id,
            TrackerMonthlyShare.month == month,
        )
    }


def balance_for_tracker(tracker: Tracker, expenses: list[Expense], share_overrides: dict[int, Decimal] | None = None) -> dict[str, Any]:
    shares = normalized_shares(tracker.members, share_overrides)
    shared_expenses = [expense for expense in expenses if expense.is_shared]
    shared_total = sum((Decimal(expense.amount) for expense in shared_expenses), money("0"))
    paid_shared: dict[int, Money] = defaultdict(lambda: money("0"))
    paid_individual: dict[int, Money] = defaultdict(lambda: money("0"))

    for expense in expenses:
        if expense.is_shared:
            paid_shared[expense.paid_by_id] += Decimal(expense.amount)
        else:
            paid_individual[expense.paid_by_id] += Decimal(expense.amount)

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


def period_options(session: Session, tracker_id: int) -> dict[str, Any]:
    rows = (
        session.query(
            extract("year", Expense.date).label("year"),
            extract("month", Expense.date).label("month"),
        )
        .filter(Expense.tracker_id == tracker_id)
        .group_by("year", "month")
        .order_by("year", "month")
        .all()
    )
    months = [f"{int(row.year):04d}-{int(row.month):02d}" for row in rows]
    years = sorted({int(row.year) for row in rows})
    return {"months": months, "years": years}


def monthly_totals_for_year(session: Session, tracker_id: int, year: int) -> list[dict[str, Any]]:
    rows = (
        session.query(extract("month", Expense.date).label("month"), func.sum(Expense.amount).label("total"))
        .filter(Expense.tracker_id == tracker_id, extract("year", Expense.date) == year)
        .group_by("month")
        .order_by("month")
        .all()
    )
    return [
        {
            "month": f"{year:04d}-{int(row.month):02d}",
            "total": float(money(row.total or 0)),
        }
        for row in rows
    ]
