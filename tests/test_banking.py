from __future__ import annotations

import unittest
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.banking.service import create_bank_connection, import_bank_transactions, list_review_bank_transactions
from app.models import Base, BankTransaction, Category, Expense, Tracker, TrackerMember, User
from app.schemas import BankTransactionImportItem


class FakePlaidClient:
    def exchange_public_token(self, _public_token: str) -> dict[str, str]:
        return {"access_token": "access-test", "item_id": "item-test"}

    def get_accounts(self, _access_token: str) -> list[dict[str, object]]:
        return [
            {
                "account_id": "account-test",
                "name": "Scotia Chequing",
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
                    "transaction_id": "txn-outgoing",
                    "account_id": "account-test",
                    "date": "2026-07-10",
                    "authorized_date": "2026-07-09",
                    "name": "METRO",
                    "merchant_name": "Metro",
                    "amount": 42.5,
                    "iso_currency_code": "CAD",
                    "pending": False,
                },
                {
                    "transaction_id": "txn-inflow",
                    "account_id": "account-test",
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

            create_bank_connection(session, tracker, user, "public-test", "Scotiabank", FakePlaidClient())

            rows = {transaction.provider_transaction_id: transaction for transaction in session.query(BankTransaction).all()}
            self.assertEqual(rows["txn-outgoing"].status, "ready")
            self.assertEqual(rows["txn-outgoing"].amount, Decimal("42.500"))
            self.assertEqual(rows["txn-inflow"].status, "ready")
            review_rows = list_review_bank_transactions(session, tracker.id, 30)
            self.assertEqual([row.provider_transaction_id for row in review_rows], ["txn-outgoing"])

    def test_import_uses_connected_user_as_default_payer_and_requires_category(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x", default_currency="CAD")
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            member = TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("100"), user=user, tracker=tracker)
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add_all([user, tracker, member, category])
            session.flush()
            create_bank_connection(session, tracker, user, "public-test", "Scotiabank", FakePlaidClient())
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
            create_bank_connection(session, tracker, user, "public-test", "Scotiabank", FakePlaidClient())
            transaction = session.query(BankTransaction).filter(BankTransaction.provider_transaction_id == "txn-outgoing").one()
            transaction.status = "ignored"
            review_rows = list_review_bank_transactions(session, tracker.id, 30)
            self.assertEqual([row.provider_transaction_id for row in review_rows], ["txn-outgoing"])

            result = import_bank_transactions(
                session,
                tracker,
                user,
                [BankTransactionImportItem(transaction_id=transaction.id, category_id=category.id)],
            )

            self.assertEqual(result, {"imported": 1, "skipped": []})
            self.assertEqual(transaction.status, "imported")
            self.assertEqual(list_review_bank_transactions(session, tracker.id, 30), [])


if __name__ == "__main__":
    unittest.main()
