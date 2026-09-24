from __future__ import annotations

import unittest
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from litestar.exceptions import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.banking.service import (
    create_bank_connection,
    ignore_bank_transaction,
    import_bank_transactions,
    list_review_bank_transactions,
    load_bank_connection_for_user,
    sync_bank_connection,
)
from app.db import ensure_bank_transaction_columns
from app.models import Base, BankTransaction, Category, Expense, Tracker, TrackerMember, User, utcnow
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
                    "date": (utcnow().date() - timedelta(days=1)).isoformat(),
                    "authorized_date": (utcnow().date() - timedelta(days=2)).isoformat(),
                    "name": "METRO",
                    "merchant_name": "Metro",
                    "amount": 42.5,
                    "iso_currency_code": "CAD",
                    "pending": False,
                },
                {
                    "transaction_id": self.transaction_id("inflow"),
                    "account_id": f"account-{self.suffix}",
                    "date": utcnow().date().isoformat(),
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

    def test_plaid_sync_stages_transactions_for_review(self) -> None:
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
            self.assertIsNone(rows["txn-outgoing"].ignored_at)
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

    def test_ignore_persists_across_sessions_and_bank_updates_and_blocks_import(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x", default_currency="CAD")
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            member = TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("100"), user=user, tracker=tracker)
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add_all([user, tracker, member, category])
            session.flush()
            connection = create_bank_connection(session, tracker, user, "public-test", "Mybank", FakePlaidClient())
            transaction = session.query(BankTransaction).filter(BankTransaction.provider_transaction_id == "txn-outgoing").one()
            transaction_id, connection_id = transaction.id, connection.id

            ignore_bank_transaction(session, tracker.id, transaction.id, user)
            self.assertIsNotNone(transaction.ignored_at)
            self.assertEqual(list_review_bank_transactions(session, tracker.id, user, 30), [])
            session.commit()

        with self.Session() as session:
            user = session.get(User, 1)
            tracker = session.get(Tracker, 1)
            transaction = session.get(BankTransaction, transaction_id)
            ignored_at = transaction.ignored_at
            self.assertIsNotNone(ignored_at)
            ignore_bank_transaction(session, tracker.id, transaction.id, user)
            self.assertEqual(transaction.ignored_at, ignored_at)

            connection = load_bank_connection_for_user(session, tracker.id, connection_id, user)
            plaid_client = FakePlaidClient()
            modified = plaid_client.sync_transactions("access-test")["added"][0]
            modified["name"] = "Updated merchant"
            modified["amount"] = 50
            with patch.object(plaid_client, "sync_transactions", return_value={"modified": [modified], "next_cursor": "cursor-2", "has_more": False}):
                sync_bank_connection(session, connection, plaid_client)

            session.expire_all()
            self.assertEqual(transaction.name, "Updated merchant")
            self.assertEqual(transaction.amount, Decimal("50.000"))
            self.assertEqual(transaction.ignored_at, ignored_at)
            self.assertEqual(list_review_bank_transactions(session, tracker.id, user, 730), [])
            result = import_bank_transactions(
                session,
                tracker,
                user,
                [BankTransactionImportItem(transaction_id=transaction.id, category_id=1)],
            )
            self.assertEqual(result, {"imported": 0, "skipped": [{"transaction_id": transaction_id, "reason": "Transaction was ignored"}]})
            self.assertEqual(session.query(Expense).count(), 0)

    def test_ignore_rejects_already_imported_transactions(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x", default_currency="CAD")
            tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
            member = TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("100"), user=user, tracker=tracker)
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add_all([user, tracker, member, category])
            session.flush()
            create_bank_connection(session, tracker, user, "public-test", "Mybank", FakePlaidClient())
            transaction = session.query(BankTransaction).filter(BankTransaction.provider_transaction_id == "txn-outgoing").one()
            import_bank_transactions(session, tracker, user, [BankTransactionImportItem(transaction_id=transaction.id, category_id=category.id)])

            with self.assertRaises(HTTPException) as context:
                ignore_bank_transaction(session, tracker.id, transaction.id, user)

            self.assertEqual(context.exception.status_code, 409)
            self.assertIsNone(transaction.ignored_at)
            self.assertEqual(session.query(Expense).count(), 1)

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

            for other_user in [marcos, admin]:
                with self.subTest(user=other_user.name):
                    with self.assertRaises(HTTPException) as context:
                        ignore_bank_transaction(session, tracker.id, gabriela_transaction.id, other_user)
                    self.assertEqual(context.exception.status_code, 404)
                    self.assertIsNone(gabriela_transaction.ignored_at)

            other_tracker = Tracker(id=2, name="Other", default_currency="CAD", created_by_id=marcos.id)
            session.add_all([other_tracker, TrackerMember(tracker_id=2, user_id=marcos.id, role="owner", user=marcos, tracker=other_tracker)])
            session.flush()
            with self.assertRaises(HTTPException) as tracker_context:
                ignore_bank_transaction(session, other_tracker.id, marcos_transaction.id, marcos)
            self.assertEqual(tracker_context.exception.status_code, 404)
            self.assertIsNone(marcos_transaction.ignored_at)

            with self.assertRaises(HTTPException) as missing_context:
                ignore_bank_transaction(session, tracker.id, 99999, marcos)
            self.assertEqual(missing_context.exception.status_code, 404)

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


class BankingMigrationTests(unittest.TestCase):
    def test_adds_nullable_ignore_timestamp_without_hiding_legacy_transactions(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE bank_transactions (id INTEGER PRIMARY KEY, status VARCHAR(40) NOT NULL)"))
            connection.execute(text("INSERT INTO bank_transactions (id, status) VALUES (1, 'ready'), (2, 'ignored'), (3, 'imported')"))

        with patch("app.db.engine", engine):
            ensure_bank_transaction_columns()
            ensure_bank_transaction_columns()

        self.assertIn("ignored_at", {column["name"] for column in inspect(engine).get_columns("bank_transactions")})
        with engine.connect() as connection:
            rows = connection.execute(text("SELECT status, ignored_at FROM bank_transactions ORDER BY id")).all()
        self.assertEqual(rows, [("ready", None), ("ignored", None), ("imported", None)])


if __name__ == "__main__":
    unittest.main()
