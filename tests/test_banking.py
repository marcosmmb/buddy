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
    list_ignored_bank_transactions,
    restore_bank_transaction,
    load_bank_connection_for_user,
    sync_bank_connection,
    upsert_accounts,
)
from app.db import ensure_bank_account_columns, ensure_bank_transaction_columns
from app.models import Base, BankAccount, BankConnection, BankTransaction, Category, Expense, Tracker, TrackerMember, User, utcnow
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

    def test_reconnection_refresh_reuses_account_and_preserves_transaction_history(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="owner@example.test", name="Owner", password_hash="x")
            tracker = Tracker(id=1, name="Home", created_by_id=1)
            category = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
            session.add_all([user, tracker, category, TrackerMember(tracker_id=1, user_id=1, role="owner", user=user, tracker=tracker)])
            session.flush()
            plaid = FakePlaidClient()
            connection = create_bank_connection(session, tracker, user, "public-test", "Mybank", plaid)
            account = session.query(BankAccount).one()
            original_account_id, original_connection_id = account.id, connection.id
            account.enabled = False
            outgoing = session.query(BankTransaction).filter_by(provider_transaction_id="txn-outgoing").one()
            import_bank_transactions(session, tracker, user, [BankTransactionImportItem(transaction_id=outgoing.id, category_id=1)])
            expense_id = outgoing.expense_id
            ignored = BankTransaction(bank_account_id=account.id, provider_transaction_id="txn-ignored", date=utcnow().date(), amount=10, ignored_at=utcnow())
            session.add(ignored)
            session.commit()
            ignored_at = ignored.ignored_at
            encrypted_token = connection.encrypted_access_token
            new_account = plaid.get_accounts("access-test")[0]
            new_account["account_id"] = "rotated-account-id"
            new_account["name"] = "Updated Checking Name"
            old_transaction = plaid.sync_transactions("access-test")["added"][0]
            new_transaction = {**old_transaction, "transaction_id": "txn-new", "account_id": "rotated-account-id"}
            with patch.object(plaid, "get_accounts", return_value=[new_account]) as get_accounts, patch.object(plaid, "sync_transactions", return_value={
                "added": [new_transaction], "modified": [old_transaction], "next_cursor": "refreshed-cursor", "has_more": False,
            }) as sync:
                sync_bank_connection(session, connection, plaid, refresh_accounts=True)
                get_accounts.assert_called_once_with("access-test")
                sync.assert_called_once_with("access-test", "cursor-1")
            session.commit()
            session.expire_all()
            self.assertEqual(session.query(BankConnection).count(), 1)
            self.assertEqual(session.query(BankAccount).count(), 1)
            self.assertEqual(connection.id, original_connection_id)
            self.assertEqual(connection.encrypted_access_token, encrypted_token)
            self.assertEqual(connection.provider_item_id, "item-test")
            self.assertEqual(account.id, original_account_id)
            self.assertEqual(account.provider_account_id, "rotated-account-id")
            self.assertEqual(account.name, "Updated Checking Name")
            self.assertFalse(account.enabled)
            self.assertEqual(outgoing.bank_account_id, original_account_id)
            self.assertEqual(outgoing.status, "imported")
            self.assertEqual(outgoing.expense_id, expense_id)
            self.assertEqual(ignored.ignored_at, ignored_at)
            self.assertEqual(ignored.bank_account_id, original_account_id)
            self.assertEqual(session.query(BankTransaction).filter_by(provider_transaction_id="txn-new").one().bank_account_id, original_account_id)
            self.assertEqual(session.query(Expense).count(), 1)

    def test_account_refresh_uses_persistent_identity_when_other_details_change(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="owner@example.test", name="Owner", password_hash="x")
            tracker = Tracker(id=1, name="Home", created_by_id=1)
            session.add_all([user, tracker])
            session.flush()
            connection = BankConnection(tracker_id=1, user_id=1, provider_item_id="item-test", encrypted_access_token="unused")
            session.add(connection)
            session.flush()
            upsert_accounts(session, connection, [{"account_id": "old-id", "persistent_account_id": "stable-id", "name": "Old name", "mask": "1234"}])
            account_id = session.query(BankAccount).one().id
            upsert_accounts(session, connection, [{"account_id": "new-id", "persistent_account_id": "stable-id", "name": "New name", "mask": "5678"}], reconcile_existing=True)
            account = session.query(BankAccount).one()
            self.assertEqual(account.id, account_id)
            self.assertEqual(account.provider_account_id, "new-id")
            self.assertEqual(account.mask, "5678")

    def test_account_refresh_keeps_ambiguous_or_different_accounts_separate(self) -> None:
        details = {"mask": "1234", "type": "depository", "subtype": "checking", "balances": {"iso_currency_code": "CAD"}}
        scenarios = [
            ([{"account_id": "old-1"}, {"account_id": "old-2"}], [{"account_id": "new-1"}], 3),
            ([{"account_id": "old-1"}], [{"account_id": "new-1"}, {"account_id": "new-2"}], 3),
            ([{"account_id": "old-1"}], [{"account_id": "old-1"}, {"account_id": "new-1"}], 2),
            ([{"account_id": "old-1", "persistent_account_id": "stable-1"}], [{"account_id": "new-1", "persistent_account_id": "stable-2"}], 2),
            ([{"account_id": "old-1", "persistent_account_id": "stable-1"}], [{"account_id": "new-1", "persistent_account_id": "stable-1"}, {"account_id": "new-2", "persistent_account_id": "stable-1"}], 3),
            ([{"account_id": "old-1", "mask": ""}], [{"account_id": "new-1", "mask": ""}], 2),
        ]
        for old_rows, new_rows, expected_count in scenarios:
            with self.subTest(old_rows=old_rows, new_rows=new_rows), self.Session() as session:
                user = User(id=1, email="owner@example.test", name="Owner", password_hash="x")
                tracker = Tracker(id=1, name="Home", created_by_id=1)
                session.add_all([user, tracker])
                session.flush()
                connection = BankConnection(tracker_id=1, user_id=1, provider_item_id="item-test", encrypted_access_token="unused")
                session.add(connection)
                session.flush()
                upsert_accounts(session, connection, [{**details, **row} for row in old_rows])
                upsert_accounts(session, connection, [{**details, **row} for row in new_rows], reconcile_existing=True)
                self.assertEqual(session.query(BankAccount).count(), expected_count)
                self.assertEqual(session.query(BankAccount).filter_by(provider_account_id="old-1").count(), 1)

    def test_account_refresh_never_matches_accounts_from_another_connection(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="owner@example.test", name="Owner", password_hash="x")
            tracker = Tracker(id=1, name="Home", created_by_id=1)
            session.add_all([user, tracker])
            session.flush()
            first = BankConnection(tracker_id=1, user_id=1, provider_item_id="first", encrypted_access_token="unused")
            second = BankConnection(tracker_id=1, user_id=1, provider_item_id="second", encrypted_access_token="unused")
            session.add_all([first, second])
            session.flush()
            upsert_accounts(session, first, [{"account_id": "old-id", "persistent_account_id": "stable-id"}])
            upsert_accounts(session, second, [{"account_id": "new-id", "persistent_account_id": "stable-id"}], reconcile_existing=True)
            self.assertEqual(session.query(BankAccount).count(), 2)
            self.assertEqual(session.query(BankAccount).filter_by(provider_account_id="old-id").one().bank_connection_id, first.id)

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

            self.assertEqual([row.id for row in list_ignored_bank_transactions(session, tracker.id, user, 30)], [transaction_id])
            restore_bank_transaction(session, tracker.id, transaction_id, user)
            restore_bank_transaction(session, tracker.id, transaction_id, user)
            session.commit()
            session.expire_all()
            self.assertIsNone(transaction.ignored_at)
            self.assertEqual(list_ignored_bank_transactions(session, tracker.id, user, 30), [])
            self.assertEqual([row.id for row in list_review_bank_transactions(session, tracker.id, user, 30)], [transaction_id])
            result = import_bank_transactions(session, tracker, user, [BankTransactionImportItem(transaction_id=transaction_id, category_id=1)])
            self.assertEqual(result, {"imported": 1, "skipped": []})

    def test_ignored_transactions_use_transaction_date_and_same_review_window(self) -> None:
        with self.Session() as session:
            user = User(id=1, email="marcos@example.test", name="Marcos", password_hash="x")
            tracker = Tracker(id=1, name="Home", created_by_id=1)
            member = TrackerMember(tracker_id=1, user_id=1, role="owner", user=user, tracker=tracker)
            session.add_all([user, tracker, member])
            session.flush()
            create_bank_connection(session, tracker, user, "public-test", "Mybank", FakePlaidClient())
            transaction = session.query(BankTransaction).filter_by(provider_transaction_id="txn-outgoing").one()
            transaction.date = utcnow().date() - timedelta(days=7)
            ignore_bank_transaction(session, tracker.id, transaction.id, user)
            self.assertEqual([row.id for row in list_ignored_bank_transactions(session, tracker.id, user, 8)], [transaction.id])
            self.assertEqual(list_ignored_bank_transactions(session, tracker.id, user, 7), [])
            restore_bank_transaction(session, tracker.id, transaction.id, user)
            self.assertEqual(list_ignored_bank_transactions(session, tracker.id, user, 8), [])
            self.assertEqual(list_review_bank_transactions(session, tracker.id, user, 7), [])
            self.assertEqual([row.id for row in list_review_bank_transactions(session, tracker.id, user, 8)], [transaction.id])

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
            with self.assertRaises(HTTPException) as restore_context:
                restore_bank_transaction(session, tracker.id, transaction.id, user)
            self.assertEqual(restore_context.exception.status_code, 409)
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
    def test_adds_persistent_account_identity_without_changing_existing_ids(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE bank_accounts (id INTEGER PRIMARY KEY, provider_account_id VARCHAR(160) NOT NULL)"))
            connection.execute(text("INSERT INTO bank_accounts (id, provider_account_id) VALUES (7, 'old-id')"))
        with patch("app.db.engine", engine):
            ensure_bank_account_columns()
            ensure_bank_account_columns()
        with engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT id, provider_account_id, persistent_account_id FROM bank_accounts")).all(), [(7, "old-id", None)])

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
