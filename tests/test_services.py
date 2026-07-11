from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, Category, Expense, Tracker, TrackerMember, User
from app.services import (
    balance_for_tracker,
    member_breakdown_for_expenses,
    money,
    monthly_totals_for_year,
    normalized_shares,
    overview_for_expenses,
    period_options,
    serialize_expense,
)
from tests.helpers import make_category, make_expense, make_member, make_tracker, make_user


class MoneyTests(unittest.TestCase):
    def test_money_rounds_half_up_to_cents(self) -> None:
        self.assertEqual(money("1.005"), Decimal("1.01"))
        self.assertEqual(money("1.004"), Decimal("1.00"))


class SummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.marcos = make_user(1, "Marcos")
        self.gabriela = make_user(2, "Gabriela")
        self.members = [make_member(self.marcos, 50, "owner"), make_member(self.gabriela, 50)]
        self.tracker = make_tracker(*self.members)
        self.groceries = make_category(1, "Groceries", "#f1b84b")
        self.travel = make_category(2, "Travel", "#0369a1")

    def test_overview_summarizes_totals_and_preserves_category_colors(self) -> None:
        expenses = [
            make_expense("100.00", self.marcos, self.groceries, expense_id=1, shared=True),
            make_expense("25.50", self.gabriela, self.travel, expense_id=2, shared=False),
        ]

        overview = overview_for_expenses(expenses)

        self.assertEqual(overview["total"], 125.5)
        self.assertEqual(
            overview["by_category"],
            [
                {"name": "Groceries", "color": "#f1b84b", "total": 100.0},
                {"name": "Travel", "color": "#0369a1", "total": 25.5},
            ],
        )
        self.assertEqual(
            overview["by_person"],
            [
                {"name": "Gabriela", "shared": 0.0, "individual": 25.5, "total": 25.5},
                {"name": "Marcos", "shared": 100.0, "individual": 0.0, "total": 100.0},
            ],
        )
        self.assertEqual(overview["by_person_category"][0]["category_color"], "#0369a1")

    def test_balance_splits_shared_expenses_and_minimizes_settlements(self) -> None:
        expenses = [
            make_expense("200.00", self.marcos, self.groceries, expense_id=1, shared=True),
            make_expense("40.00", self.gabriela, self.travel, expense_id=2, shared=False),
        ]

        balance = balance_for_tracker(self.tracker, expenses)

        self.assertEqual(balance["shared_total"], 200.0)
        self.assertEqual(
            balance["settlements"],
            [{"from_user_id": 2, "from": "Gabriela", "to_user_id": 1, "to": "Marcos", "amount": 100.0}],
        )
        rows = {row["user_id"]: row for row in balance["rows"]}
        self.assertEqual(rows[1]["expected_shared"], 100.0)
        self.assertEqual(rows[1]["paid_shared"], 200.0)
        self.assertEqual(rows[2]["paid_individual"], 40.0)

    def test_balance_uses_monthly_share_overrides(self) -> None:
        expense = make_expense("200.00", self.marcos, self.groceries, shared=True)

        balance = balance_for_tracker(self.tracker, [expense], {1: Decimal("70"), 2: Decimal("30")})

        self.assertEqual(
            balance["settlements"],
            [{"from_user_id": 2, "from": "Gabriela", "to_user_id": 1, "to": "Marcos", "amount": 60.0}],
        )

    def test_member_breakdown_allocates_shared_expenses_by_month(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as session:
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            marcos = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x")
            gabriela = User(id=2, email="gabriela@example.test", name="Gabriela", password_hash="x")
            session.add_all([tracker, marcos, gabriela])
            session.add_all(
                [
                    TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("50"), user=marcos, tracker=tracker),
                    TrackerMember(tracker_id=1, user_id=2, role="member", share_percent=Decimal("50"), user=gabriela, tracker=tracker),
                ]
            )
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add(category)
            session.flush()
            expense = Expense(
                id=1,
                tracker_id=1,
                category_id=1,
                paid_by_id=1,
                date=date(2026, 7, 1),
                amount=Decimal("100.00"),
                currency="CAD",
                description="Market",
                is_shared=True,
                category=category,
                paid_by=marcos,
                tracker=tracker,
            )
            session.add(expense)
            session.flush()

            rows = member_breakdown_for_expenses(session, tracker, [expense])

        self.assertEqual(
            rows,
            [
                {
                    "user_id": 2,
                    "name": "Gabriela",
                    "responsibility_shared": 50.0,
                    "responsibility_individual": 0.0,
                    "responsibility_total": 50.0,
                    "paid_shared": 0.0,
                    "paid_individual": 0.0,
                    "paid_total": 0.0,
                },
                {
                    "user_id": 1,
                    "name": "Marcos",
                    "responsibility_shared": 50.0,
                    "responsibility_individual": 0.0,
                    "responsibility_total": 50.0,
                    "paid_shared": 100.0,
                    "paid_individual": 0.0,
                    "paid_total": 100.0,
                },
            ],
        )

    def test_normalized_shares_defaults_to_equal_when_all_zero(self) -> None:
        shares = normalized_shares([make_member(self.marcos, 0), make_member(self.gabriela, 0)])

        self.assertEqual(shares, {1: Decimal("0.5"), 2: Decimal("0.5")})

    def test_serialize_expense_uses_tracker_currency(self) -> None:
        expense = make_expense("10.00", self.marcos, self.groceries, tracker=self.tracker, currency="USD")

        serialized = serialize_expense(expense)

        self.assertEqual(serialized["currency"], "CAD")
        self.assertEqual(serialized["category_color"], "#f1b84b")


class QueryHelperTests(unittest.TestCase):
    def test_period_options_and_monthly_totals_use_expense_dates(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        with Session() as session:
            user = User(id=1, email="admin@example.test", name="Admin", password_hash="x")
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add_all([user, tracker, category])
            session.add_all(
                [
                    Expense(tracker_id=1, category_id=1, paid_by_id=1, date=date(2026, 6, 1), amount=Decimal("12.34"), currency="CAD", description="", is_shared=True),
                    Expense(tracker_id=1, category_id=1, paid_by_id=1, date=date(2026, 7, 1), amount=Decimal("20.00"), currency="CAD", description="", is_shared=True),
                    Expense(tracker_id=1, category_id=1, paid_by_id=1, date=date(2025, 7, 1), amount=Decimal("5.00"), currency="CAD", description="", is_shared=True),
                ]
            )
            session.commit()

            self.assertEqual(period_options(session, 1), {"months": ["2025-07", "2026-06", "2026-07"], "years": [2025, 2026]})
            self.assertEqual(monthly_totals_for_year(session, 1, 2026), [{"month": "2026-06", "total": 12.34}, {"month": "2026-07", "total": 20.0}])


if __name__ == "__main__":
    unittest.main()
