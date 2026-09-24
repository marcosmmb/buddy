from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.banking.service import ignore_bank_transaction, import_bank_transactions, list_review_bank_transactions, sync_bank_connection
from app.models import Base, BankConnection, BankTransaction, Category, Expense, Tracker, TrackerMember, User, utcnow
from app.schemas import BankTransactionImportItem
from app.security import verify_password
from scripts.seed_demo import seed_demo


class SeedDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        self.addCleanup(engine.dispose)

        @event.listens_for(engine, "connect")
        def foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
        self.session = sessionmaker(bind=engine)()
        self.addCleanup(self.session.close)
        self.owner = User(email="owner@example.test", name="Owner", password_hash="unused", default_currency="CAD")
        self.session.add(self.owner)
        self.session.flush()

    def test_creates_isolated_demo_with_recent_review_rows_and_useful_totals(self) -> None:
        real_tracker = Tracker(name="Real tracker", created_by_id=self.owner.id)
        self.session.add(real_tracker)
        self.session.flush()
        real_category = Category(tracker_id=real_tracker.id, name="Real category")
        self.session.add(real_category)
        self.session.flush()
        real_expense = Expense(tracker_id=real_tracker.id, category_id=real_category.id, paid_by_id=self.owner.id,
                               date=utcnow().date(), amount=Decimal("123.45"), currency="CAD", description="Keep me")
        self.session.add(real_expense)

        tracker, created = seed_demo(self.session, self.owner.email)
        self.session.commit()
        self.assertTrue(created)
        self.assertNotEqual(tracker.id, real_tracker.id)
        self.assertEqual(real_expense.amount, Decimal("123.45"))
        self.assertEqual(real_expense.description, "Keep me")
        self.assertEqual(tracker.default_currency, "CAD")
        self.assertEqual(sum(member.share_percent for member in tracker.members), 100)
        housemate = next(member.user for member in tracker.members if member.user_id != self.owner.id)
        self.assertFalse(housemate.is_active)
        self.assertFalse(verify_password("disabled-demo-account", housemate.password_hash))
        self.assertEqual(len(tracker.expenses), 10)
        self.assertEqual({expense.is_shared for expense in tracker.expenses}, {True, False})
        self.assertEqual(len({expense.date.strftime("%Y-%m") for expense in tracker.expenses}), 2)
        self.assertEqual(len(list_review_bank_transactions(self.session, tracker.id, self.owner, 8)), 8)
        self.assertEqual(list_review_bank_transactions(self.session, real_tracker.id, self.owner, 8), [])
        self.assertEqual(self.session.query(BankTransaction).count(), 10)

    def test_rerun_and_demo_sync_preserve_edits_imports_and_ignored_rows(self) -> None:
        tracker, _ = seed_demo(self.session, self.owner.email)
        rows = list_review_bank_transactions(self.session, tracker.id, self.owner, 8)
        ignored_id, imported_id = rows[0].id, rows[1].id
        ignore_bank_transaction(self.session, tracker.id, ignored_id, self.owner)
        result = import_bank_transactions(self.session, tracker, self.owner, [
            BankTransactionImportItem(transaction_id=imported_id, category_id=tracker.categories[0].id, is_shared=True),
        ])
        self.assertEqual(result["imported"], 1)
        tracker.name = "My edited demo"
        self.session.commit()
        counts = {model: self.session.query(model).count() for model in (Tracker, TrackerMember, User, Expense, BankTransaction)}
        same_tracker, created = seed_demo(self.session, self.owner.email)
        connection = self.session.query(BankConnection).filter_by(tracker_id=tracker.id).one()
        with patch("app.banking.service.PlaidClient") as plaid, patch("app.banking.service.decrypt_token") as decrypt:
            self.assertEqual(sync_bank_connection(self.session, connection), {"added": 0, "modified": 0, "removed": 0})
            plaid.assert_not_called()
            decrypt.assert_not_called()
        self.session.commit()
        self.assertFalse(created)
        self.assertEqual(same_tracker.id, tracker.id)
        self.assertEqual(same_tracker.name, "My edited demo")
        for model, count in counts.items():
            self.assertEqual(self.session.query(model).count(), count)
        remaining = list_review_bank_transactions(self.session, tracker.id, self.owner, 8)
        self.assertEqual(len(remaining), 6)
        self.assertTrue({ignored_id, imported_id}.isdisjoint(row.id for row in remaining))
        self.assertTrue(self.session.get(BankTransaction, imported_id).expense.is_shared)

    def test_recreating_deleted_demo_reuses_disabled_member_and_handles_january(self) -> None:
        tracker, _ = seed_demo(self.session, self.owner.email, date(2026, 1, 1))
        self.session.commit()
        self.assertEqual({expense.date.strftime("%Y-%m") for expense in tracker.expenses}, {"2025-12", "2026-01"})
        self.session.delete(tracker)
        self.session.commit()
        self.session.expire_all()
        fresh, created = seed_demo(self.session, self.owner.email)
        self.session.commit()
        self.assertTrue(created)
        self.assertEqual(self.session.query(User).count(), 2)
        self.assertEqual(len(list_review_bank_transactions(self.session, fresh.id, self.owner, 8)), 8)

    def test_unknown_or_disabled_owner_does_not_create_demo_data(self) -> None:
        for email in ["missing@example.test", self.owner.email]:
            if email == self.owner.email:
                self.owner.is_active = False
                self.session.flush()
            with self.assertRaisesRegex(ValueError, "Active user not found"):
                seed_demo(self.session, email)
        self.assertEqual(self.session.query(Tracker).count(), 0)
        self.assertEqual(self.session.query(User).count(), 1)


class SeedDemoCommandTests(unittest.TestCase):
    def test_cli_and_docker_stdin_form_use_existing_database_and_owner(self) -> None:
        root = Path(__file__).resolve().parent.parent
        script = root / "scripts" / "seed_demo.py"
        with tempfile.TemporaryDirectory() as directory:
            database_url = f"sqlite:///{Path(directory) / 'demo.sqlite3'}"
            engine = create_engine(database_url)
            try:
                Base.metadata.create_all(engine)
                with sessionmaker(bind=engine)() as session:
                    session.add(User(email="reviewer@example.test", name="Reviewer", password_hash="unused"))
                    session.commit()
                env = {**os.environ, "DATABASE_URL": database_url, "BUDDY_DEMO_USER": "reviewer@example.test"}
                first = subprocess.run([sys.executable, str(script)], cwd=root, env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                self.assertIn("Created tracker", first.stdout)
                second = subprocess.run([sys.executable, "-"], input=script.read_text(), cwd=root, env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
                self.assertIn("Kept existing tracker", second.stdout)
                with sessionmaker(bind=engine)() as session:
                    self.assertEqual(session.query(Tracker).count(), 1)
                    self.assertEqual(session.query(Expense).count(), 10)
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
