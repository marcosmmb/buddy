from __future__ import annotations

import unittest
from decimal import Decimal

from litestar.exceptions import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.banking.service import (
    create_bank_connection,
    import_bank_transactions,
    list_review_bank_transactions,
    load_bank_connection_for_user,
)
from app.models import Base, BankTransaction, Category, Expense, Tracker, TrackerMember, User
from app.schemas import BankTransactionImportItem


class FakePlaidClient:
    def __init__(self, suffix: str = "test") -> None:
        self.suffix = suffix

    def exchange_public_token(self, _public_token: str) -> dict[str, str]:
        return {"access_token": f"access-{self.suffix}", "item_id": f"item-{self.suffix}"}

    def transaction_id(self, name: str) -> str:
        return f"txn-{name}" if self.suffix == "test" else f"txn-{name}-{self.suffix}"

    def get_accounts(self, _access_token: str) -> list[dict[str, object]]:
        return [
            {
                "account_id": f"account-{self.suffix}",
                "name": "MyBank Chequing",
                "mask": "1234",
                "type": "depository",
                "subtype": "checking",
                "balances": {"iso_currency_code": "CAD"},
            }
        ]

    def sync_transactions(self, _access_token: str, _cursor: str | None = None) -> dict[str, object]:
        return {
            "added": [
                {
                    "transaction_id": self.transaction_id("outgoing"),
                    "account_id": f"account-{self.suffix}",
                    "date": "2026-07-10",
                    "authorized_date": "2026-07-09",
                    "name": "METRO",
                    "merchant_name": "Metro",
                    "amount": 42.5,
                    "iso_currency_code": "CAD",
                    "pending": False,
                },
                {
                    "transaction_id": self.transaction_id("inflow"),
                    "account_id": f"account-{self.suffix}",
                    "date": "2026-07-11",
                    "name": "Payroll",
                    "merchant_name": None,
                    "amount": -1000,
                    "iso_currency_code": "CAD",
                    "pending": False,
                },
            ],
            "modified": [],
            "removed": [],
            "next_cursor": "cursor-1",
            "has_more": False,
        }


class BankingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

    def test_plaid_sync_stages_transactions_without_ignore_state(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x", default_currency="CAD")
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            member = TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("100"), user=user, tracker=tracker)
            session.add_all([user, tracker, member])
            session.flush()

            create_bank_connection(session, tracker, user, "public-test", "Mybank", FakePlaidClient())

            rows = {transaction.provider_transaction_id: transaction for transaction in session.query(BankTransaction).all()}
            self.assertEqual(rows["txn-outgoing"].status, "ready")
            self.assertEqual(rows["txn-outgoing"].amount, Decimal("42.500"))
            self.assertEqual(rows["txn-inflow"].status, "ready")
            review_rows = list_review_bank_transactions(session, tracker.id, user, 30)
            self.assertEqual([row.provider_transaction_id for row in review_rows], ["txn-outgoing"])

    def test_import_uses_connected_user_as_default_payer_and_requires_category(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x", default_currency="CAD")
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            member = TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("100"), user=user, tracker=tracker)
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add_all([user, tracker, member, category])
            session.flush()
            create_bank_connection(session, tracker, user, "public-test", "Mybank", FakePlaidClient())
            transaction = session.query(BankTransaction).filter(BankTransaction.provider_transaction_id == "txn-outgoing").one()

            result = import_bank_transactions(
                session,
                tracker,
                user,
                [BankTransactionImportItem(transaction_id=transaction.id, category_id=category.id, description="Manual category", is_shared=True)],
            )

            self.assertEqual(result, {"imported": 1, "skipped": []})
            expense = session.query(Expense).one()
            self.assertEqual(expense.paid_by_id, user.id)
            self.assertEqual(expense.category_id, category.id)
            self.assertEqual(expense.amount, Decimal("42.500"))
            self.assertTrue(expense.is_shared)
            self.assertEqual(transaction.status, "imported")
            self.assertEqual(transaction.expense_id, expense.id)

    def test_import_allows_legacy_outgoing_transactions_with_old_ignore_status(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x", default_currency="CAD")
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            member = TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("100"), user=user, tracker=tracker)
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add_all([user, tracker, member, category])
            session.flush()
            create_bank_connection(session, tracker, user, "public-test", "Mybank", FakePlaidClient())
            transaction = session.query(BankTransaction).filter(BankTransaction.provider_transaction_id == "txn-outgoing").one()
            transaction.status = "ignored"
            review_rows = list_review_bank_transactions(session, tracker.id, user, 30)
            self.assertEqual([row.provider_transaction_id for row in review_rows], ["txn-outgoing"])

            result = import_bank_transactions(
                session,
                tracker,
                user,
                [BankTransactionImportItem(transaction_id=transaction.id, category_id=category.id)],
            )

            self.assertEqual(result, {"imported": 1, "skipped": []})
            self.assertEqual(transaction.status, "imported")
            self.assertEqual(list_review_bank_transactions(session, tracker.id, user, 30), [])

    def test_bank_connections_and_transactions_are_private_per_user_even_for_admins(self) -> None:
        with self.Session() as session:
            marcos = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x", default_currency="CAD")
            gabriela = User(id=2, email="gabriela@example.test", name="Gabriela", password_hash="x", default_currency="CAD")
            admin = User(id=3, email="admin@example.test", name="Admin", password_hash="x", default_currency="CAD", is_admin=True)
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add_all([marcos, gabriela, admin, tracker, category])
            session.add_all(
                [
                    TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("50"), user=marcos, tracker=tracker),
                    TrackerMember(tracker_id=1, user_id=2, role="member", share_percent=Decimal("50"), user=gabriela, tracker=tracker),
                ]
            )
            session.flush()
            marcos_connection = create_bank_connection(session, tracker, marcos, "public-marcos", "Marcos Bank", FakePlaidClient("marcos"))
            gabriela_connection = create_bank_connection(session, tracker, gabriela, "public-gabriela", "Gabriela Bank", FakePlaidClient("gabriela"))
            marcos_transaction = (
                session.query(BankTransaction)
                .join(BankTransaction.account)
                .filter(BankTransaction.provider_transaction_id == "txn-outgoing-marcos")
                .one()
            )
            gabriela_transaction = (
                session.query(BankTransaction)
                .join(BankTransaction.account)
                .filter(BankTransaction.provider_transaction_id == "txn-outgoing-gabriela")
                .one()
            )

            marcos_rows = list_review_bank_transactions(session, tracker.id, marcos, 30)
            gabriela_rows = list_review_bank_transactions(session, tracker.id, gabriela, 30)
            admin_rows = list_review_bank_transactions(session, tracker.id, admin, 30)

            self.assertEqual({row.account.connection.user_id for row in marcos_rows}, {marcos.id})
            self.assertEqual({row.account.connection.user_id for row in gabriela_rows}, {gabriela.id})
            self.assertEqual(admin_rows, [])

            self.assertEqual(load_bank_connection_for_user(session, tracker.id, marcos_connection.id, marcos).id, marcos_connection.id)
            with self.assertRaises(HTTPException) as member_context:
                load_bank_connection_for_user(session, tracker.id, gabriela_connection.id, marcos)
            self.assertEqual(member_context.exception.status_code, 404)
            with self.assertRaises(HTTPException) as admin_context:
                load_bank_connection_for_user(session, tracker.id, gabriela_connection.id, admin)
            self.assertEqual(admin_context.exception.status_code, 404)

            result = import_bank_transactions(
                session,
                tracker,
                marcos,
                [BankTransactionImportItem(transaction_id=gabriela_transaction.id, category_id=category.id)],
            )

            self.assertEqual(result["imported"], 0)
            self.assertEqual(result["skipped"][0]["reason"], "Transaction does not belong to this user")
            self.assertIsNone(gabriela_transaction.expense_id)
            self.assertIsNone(marcos_transaction.expense_id)


if __name__ == "__main__":
    unittest.main()
