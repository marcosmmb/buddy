from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from litestar.exceptions import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backups import BACKUP_SCHEMA_VERSION, export_tracker_backup, restore_tracker_backup
from app.models import (
    Base,
    BankAccount,
    BankConnection,
    BankTransaction,
    Category,
    CsvImportConfig,
    Expense,
    Tracker,
    TrackerMember,
    TrackerMonthlyShare,
    User,
)
def deepcopy_config(config: dict) -> dict:
    return json.loads(json.dumps(config))


class BackupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine)

    def seed_tracker(self, session) -> User:
        admin = User(id=1, email="admin@example.test", name="Admin", password_hash="x", is_admin=True)
        member = User(id=2, email="member@example.test", name="Member", password_hash="x")
        extra = User(id=3, email="extra@example.test", name="Extra", password_hash="x")
        tracker = Tracker(id=1, name="Home", default_currency="CAD", created_by_id=1)
        other_tracker = Tracker(id=2, name="Other", default_currency="USD", created_by_id=1)
        groceries = Category(id=1, tracker_id=1, name="Groceries", color="#f1b84b")
        utilities = Category(id=2, tracker_id=1, name="Utilities", color="#d99b25")
        other_category = Category(id=3, tracker_id=2, name="Other tracker category", color="#111111")
        session.add_all([admin, member, extra, tracker, other_tracker, groceries, utilities, other_category])
        session.add_all(
            [
                TrackerMember(tracker_id=1, user_id=1, role="owner", share_percent=Decimal("60")),
                TrackerMember(tracker_id=1, user_id=2, role="member", share_percent=Decimal("40")),
                TrackerMember(tracker_id=2, user_id=1, role="owner", share_percent=Decimal("100")),
                TrackerMonthlyShare(tracker_id=1, user_id=1, month="2026-07", share_percent=Decimal("55")),
                TrackerMonthlyShare(tracker_id=1, user_id=2, month="2026-07", share_percent=Decimal("45")),
                Expense(
                    tracker_id=1,
                    category_id=1,
                    paid_by_id=2,
                    date=date(2026, 7, 12),
                    amount=Decimal("12.34"),
                    currency="CAD",
                    description="Market",
                    is_shared=True,
                ),
                Expense(
                    tracker_id=1,
                    category_id=2,
                    paid_by_id=1,
                    date=date(2026, 7, 13),
                    amount=Decimal("45.678"),
                    currency="CAD",
                    description="Hydro",
                    is_shared=False,
                ),
                Expense(
                    tracker_id=2,
                    category_id=3,
                    paid_by_id=1,
                    date=date(2026, 7, 14),
                    amount=Decimal("99.99"),
                    currency="USD",
                    description="Other tracker expense",
                    is_shared=True,
                ),
                CsvImportConfig(
                    tracker_id=1,
                    name="Scotia",
                    field_map={"date": "Date", "amount": "Amount", "description": "Memo"},
                    invert_amount=True,
                    currency="CAD",
                    created_by_id=1,
                ),
            ]
        )
        session.flush()
        connection = BankConnection(
            tracker_id=1,
            user_id=1,
            provider="plaid",
            provider_item_id="item-test",
            institution_name="Scotiabank",
            encrypted_access_token="secret-token",
        )
        session.add(connection)
        session.flush()
        account = BankAccount(
            bank_connection_id=connection.id,
            provider_account_id="account-test",
            name="Chequing",
            currency="CAD",
        )
        session.add(account)
        session.flush()
        session.add(
            BankTransaction(
                bank_account_id=account.id,
                provider_transaction_id="txn-test",
                date=date(2026, 7, 15),
                amount=Decimal("10"),
                currency="CAD",
                status="ready",
            )
        )
        session.flush()
        return admin

    def test_export_contains_complete_tracker_state_and_excludes_bank_secrets(self) -> None:
        with self.Session() as session:
            self.seed_tracker(session)

            data = export_tracker_backup(session, 1)

            self.assertEqual(data["schema_version"], BACKUP_SCHEMA_VERSION)
            datetime.fromisoformat(data["exported_at"])
            self.assertEqual(data["tracker"]["name"], "Home")
            self.assertEqual(data["tracker"]["default_currency"], "CAD")
            self.assertEqual([member["email"] for member in data["members"]], ["admin@example.test", "member@example.test"])
            self.assertEqual([category["name"] for category in data["categories"]], ["Groceries", "Utilities"])
            self.assertEqual([expense["description"] for expense in data["expenses"]], ["Market", "Hydro"])
            self.assertEqual(data["expenses"][1]["amount"], "45.678")
            self.assertEqual(data["monthly_shares"][0]["month"], "2026-07")
            self.assertEqual(data["csv_configs"][0]["field_map"]["description"], "Memo")
            self.assertIn("bank_connections", data["excluded"])
            serialized = json.dumps(data)
            self.assertNotIn("secret-token", serialized)
            self.assertNotIn("txn-test", serialized)
            self.assertNotIn("Other tracker expense", serialized)

    def test_restore_replaces_only_target_tracker_and_rebuilds_relationships(self) -> None:
        with self.Session() as session:
            admin = self.seed_tracker(session)
            data = export_tracker_backup(session, 1)
            data["tracker"]["name"] = "Restored Home"
            data["tracker"]["default_currency"] = "USD"
            data["members"][1]["share_percent"] = "25"
            data["expenses"][0]["description"] = "Restored market"
            data["csv_configs"][0]["name"] = "Restored Scotia"

            result = restore_tracker_backup(session, 1, data, admin)

            self.assertEqual(result, {"status": "ok", "members": 2, "categories": 2, "expenses": 2, "monthly_shares": 2, "csv_configs": 1})
            tracker = session.get(Tracker, 1)
            self.assertEqual(tracker.name, "Restored Home")
            self.assertEqual(tracker.default_currency, "USD")
            self.assertEqual(session.query(BankConnection).filter(BankConnection.tracker_id == 1).count(), 0)
            self.assertEqual(session.query(BankTransaction).count(), 0)
            expenses = session.query(Expense).filter(Expense.tracker_id == 1).order_by(Expense.date).all()
            self.assertEqual([expense.description for expense in expenses], ["Restored market", "Hydro"])
            self.assertEqual([expense.currency for expense in expenses], ["USD", "USD"])
            self.assertEqual(expenses[0].category.name, "Groceries")
            self.assertEqual(expenses[0].paid_by.email, "member@example.test")
            restored_member = session.query(TrackerMember).filter(TrackerMember.tracker_id == 1, TrackerMember.user_id == 2).one()
            self.assertEqual(restored_member.share_percent, Decimal("25.000000"))
            self.assertEqual(session.query(CsvImportConfig).filter(CsvImportConfig.tracker_id == 1).one().name, "Restored Scotia")
            self.assertEqual(session.query(Expense).filter(Expense.tracker_id == 2).one().description, "Other tracker expense")

    def test_restore_maps_members_by_email_not_old_user_id(self) -> None:
        with self.Session() as session:
            admin = self.seed_tracker(session)
            data = export_tracker_backup(session, 1)
            data["members"][0]["id"] = 101
            data["members"][1]["id"] = 202
            data["monthly_shares"][0]["user_id"] = 101
            data["monthly_shares"][1]["user_id"] = 202
            data["expenses"][0]["paid_by_id"] = 202
            data["expenses"][1]["paid_by_id"] = 101

            restore_tracker_backup(session, 1, data, admin)

            expenses = session.query(Expense).filter(Expense.tracker_id == 1).order_by(Expense.date).all()
            self.assertEqual([expense.paid_by_id for expense in expenses], [2, 1])
            shares = session.query(TrackerMonthlyShare).filter(TrackerMonthlyShare.tracker_id == 1).order_by(TrackerMonthlyShare.user_id).all()
            self.assertEqual([share.user_id for share in shares], [1, 2])

    def test_restore_can_resolve_expense_category_by_name_when_old_id_is_absent(self) -> None:
        with self.Session() as session:
            admin = self.seed_tracker(session)
            data = export_tracker_backup(session, 1)
            del data["categories"][0]["id"]
            data["expenses"][0]["category_id"] = 999999

            restore_tracker_backup(session, 1, data, admin)

            expense = session.query(Expense).filter(Expense.description == "Market").one()
            self.assertEqual(expense.category.name, "Groceries")

    def test_restore_uses_restoring_admin_when_created_by_email_is_missing(self) -> None:
        with self.Session() as session:
            admin = self.seed_tracker(session)
            data = export_tracker_backup(session, 1)
            data["tracker"]["created_by_email"] = "not-present@example.test"
            data["csv_configs"][0]["created_by_email"] = "not-present@example.test"

            restore_tracker_backup(session, 1, data, admin)

            self.assertEqual(session.get(Tracker, 1).created_by_id, admin.id)
            self.assertEqual(session.query(CsvImportConfig).filter(CsvImportConfig.tracker_id == 1).one().created_by_id, admin.id)

    def test_restore_requires_users_to_exist(self) -> None:
        with self.Session() as session:
            admin = self.seed_tracker(session)
            data = export_tracker_backup(session, 1)
            data["members"][1]["email"] = "missing@example.test"

            with self.assertRaises(HTTPException) as context:
                restore_tracker_backup(session, 1, data, admin)

            self.assertEqual(context.exception.status_code, 400)
            self.assertIn("missing@example.test", context.exception.detail)

    def test_restore_rejects_unsupported_schema_version_without_deleting_existing_data(self) -> None:
        with self.Session() as session:
            admin = self.seed_tracker(session)
            data = export_tracker_backup(session, 1)
            data["schema_version"] = BACKUP_SCHEMA_VERSION + 1

            with self.assertRaises(HTTPException) as context:
                restore_tracker_backup(session, 1, data, admin)

            self.assertEqual(context.exception.status_code, 400)
            self.assertEqual(session.query(Expense).filter(Expense.tracker_id == 1).count(), 2)

    def test_restore_rejects_backup_without_members_categories_or_owners_before_deleting(self) -> None:
        cases = [
            ("members", [], "member"),
            ("categories", [], "category"),
            ("owner", None, "owner"),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field):
                with self.Session() as session:
                    admin = self.seed_tracker(session)
                    data = export_tracker_backup(session, 1)
                    if field == "owner":
                        for member in data["members"]:
                            member["role"] = "member"
                    else:
                        data[field] = value

                    with self.assertRaises(HTTPException) as context:
                        restore_tracker_backup(session, 1, data, admin)

                    self.assertEqual(context.exception.status_code, 400)
                    self.assertIn(expected, context.exception.detail.lower())
                    self.assertEqual(session.query(Expense).filter(Expense.tracker_id == 1).count(), 2)

    def test_restore_rejects_bad_references_and_invalid_dates(self) -> None:
        mutations = [
            ("monthly_shares", lambda data: data["monthly_shares"][0].update({"user_id": 999}), "monthly share"),
            ("missing_category", lambda data: data["expenses"][0].update({"category_id": 999, "category": "Missing"}), "category"),
            ("missing_payer", lambda data: data["expenses"][0].update({"paid_by_id": 999, "paid_by_email": "missing@example.test"}), "member"),
            ("bad_date", lambda data: data["expenses"][0].update({"date": "07/12/2026"}), "date"),
            ("bad_amount", lambda data: data["expenses"][0].update({"amount": "not-money"}), "amount"),
        ]
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                with self.Session() as session:
                    admin = self.seed_tracker(session)
                    data = export_tracker_backup(session, 1)
                    mutate(data)

                    with self.assertRaises(HTTPException) as context:
                        restore_tracker_backup(session, 1, data, admin)

                    self.assertEqual(context.exception.status_code, 400)
                    self.assertIn(expected, context.exception.detail.lower())
                    self.assertEqual(session.query(Expense).filter(Expense.tracker_id == 1).count(), 2)

    def test_restore_rejects_duplicate_backup_entities_before_deleting(self) -> None:
        mutations = [
            ("member_id", lambda data: data["members"][1].update({"id": data["members"][0]["id"]}), "member id"),
            ("member_email", lambda data: data["members"][1].update({"email": data["members"][0]["email"]}), "member email"),
            ("category_id", lambda data: data["categories"][1].update({"id": data["categories"][0]["id"]}), "category id"),
            ("category_name", lambda data: data["categories"][1].update({"name": data["categories"][0]["name"]}), "category name"),
            ("monthly_share", lambda data: data["monthly_shares"][1].update({"user_id": data["monthly_shares"][0]["user_id"]}), "monthly shares"),
            ("csv_name", lambda data: data["csv_configs"].append(deepcopy_config(data["csv_configs"][0])), "csv schema"),
        ]
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                with self.Session() as session:
                    admin = self.seed_tracker(session)
                    data = export_tracker_backup(session, 1)
                    mutate(data)

                    with self.assertRaises(HTTPException) as context:
                        restore_tracker_backup(session, 1, data, admin)

                    self.assertEqual(context.exception.status_code, 400)
                    self.assertIn(expected, context.exception.detail.lower())
                    self.assertEqual(session.query(Expense).filter(Expense.tracker_id == 1).count(), 2)

    def test_restore_rejects_unsupported_currency_and_bad_csv_field_map(self) -> None:
        mutations = [
            ("currency", lambda data: data["tracker"].update({"default_currency": "ZZZ"}), "currency"),
            ("field_map", lambda data: data["csv_configs"][0].update({"field_map": "Date"}), "field_map"),
            ("monthly_month", lambda data: data["monthly_shares"][0].update({"month": "2026-13"}), "yyyy-mm"),
        ]
        for name, mutate, expected in mutations:
            with self.subTest(name=name):
                with self.Session() as session:
                    admin = self.seed_tracker(session)
                    data = export_tracker_backup(session, 1)
                    mutate(data)

                    with self.assertRaises(HTTPException) as context:
                        restore_tracker_backup(session, 1, data, admin)

                    self.assertEqual(context.exception.status_code, 400)
                    self.assertIn(expected, context.exception.detail.lower())
                    self.assertEqual(session.query(Expense).filter(Expense.tracker_id == 1).count(), 2)

    def test_export_missing_tracker_returns_404(self) -> None:
        with self.Session() as session:
            with self.assertRaises(HTTPException) as context:
                export_tracker_backup(session, 999)

            self.assertEqual(context.exception.status_code, 404)

    def test_restore_missing_tracker_returns_404(self) -> None:
        with self.Session() as session:
            admin = self.seed_tracker(session)
            data = export_tracker_backup(session, 1)

            with self.assertRaises(HTTPException) as context:
                restore_tracker_backup(session, 999, data, admin)

            self.assertEqual(context.exception.status_code, 404)


class BackupRouteTests(unittest.TestCase):
    def run_route_script(self) -> None:
        db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        db_file.close()
        script = f"""
import json
import os
import sys
from pathlib import Path

os.environ["DATABASE_URL"] = "sqlite:///{db_file.name}"
os.environ["ADMIN_EMAIL"] = "admin@buddy.local"
os.environ["ADMIN_PASSWORD"] = "change-me-now"
os.environ["ADMIN_NAME"] = "Buddy Admin"
os.environ["LITESTAR_WARN_IMPLICIT_SYNC_TO_THREAD"] = "0"
sys.path.insert(0, {str(Path.cwd())!r})

from litestar.testing import TestClient
from app.main import app

with TestClient(app=app) as client:
    admin_login = client.post("/api/auth/login", json={{"email": "admin@buddy.local", "password": "change-me-now"}})
    assert admin_login.status_code == 201, admin_login.text
    admin_token = admin_login.json()["token"]
    admin_headers = {{"authorization": "Bearer " + admin_token}}

    user_response = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={{"name": "Member", "email": "member@example.test", "password": "password123", "default_currency": "CAD"}},
    )
    assert user_response.status_code == 201, user_response.text
    member_id = user_response.json()["id"]

    tracker_response = client.post(
        "/api/trackers",
        headers=admin_headers,
        json={{"name": "Family Home", "default_currency": "CAD", "member_ids": [member_id]}},
    )
    assert tracker_response.status_code == 201, tracker_response.text
    tracker_id = tracker_response.json()["id"]
    category_id = client.get(f"/api/trackers/{{tracker_id}}/categories", headers=admin_headers).json()[0]["id"]
    admin_id = next(member["user_id"] for member in tracker_response.json()["members"] if member["email"] == "admin@buddy.local")
    expense_response = client.post(
        f"/api/trackers/{{tracker_id}}/expenses",
        headers=admin_headers,
        json={{"date": "2026-07-12", "category_id": category_id, "amount": "19.99", "paid_by_id": admin_id, "description": "Route backup", "is_shared": True}},
    )
    assert expense_response.status_code == 201, expense_response.text

    export_response = client.get(f"/api/trackers/{{tracker_id}}/backup", headers=admin_headers)
    assert export_response.status_code == 200, export_response.text
    assert export_response.headers["content-disposition"] == 'attachment; filename="family-home-backup.json"'
    backup = export_response.json()
    assert backup["tracker"]["name"] == "Family Home"
    assert backup["expenses"][0]["description"] == "Route backup"

    restore_response = client.post(f"/api/trackers/{{tracker_id}}/backup/restore", headers=admin_headers, json=backup)
    assert restore_response.status_code == 201, restore_response.text
    assert restore_response.json()["expenses"] == 1

    no_auth_response = client.get(f"/api/trackers/{{tracker_id}}/backup")
    assert no_auth_response.status_code == 401, no_auth_response.text

    client.post("/api/auth/register", json={{"name": "Regular", "email": "regular@example.test", "password": "password123", "default_currency": "CAD"}})
    regular_login = client.post("/api/auth/login", json={{"email": "regular@example.test", "password": "password123"}})
    assert regular_login.status_code == 201, regular_login.text
    regular_headers = {{"authorization": "Bearer " + regular_login.json()["token"]}}
    forbidden_export = client.get(f"/api/trackers/{{tracker_id}}/backup", headers=regular_headers)
    assert forbidden_export.status_code == 403, forbidden_export.text
    forbidden_restore = client.post(f"/api/trackers/{{tracker_id}}/backup/restore", headers=regular_headers, json=backup)
    assert forbidden_restore.status_code == 403, forbidden_restore.text
"""
        result = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(result.returncode, 0)

    def test_backup_http_routes_permissions_headers_and_restore(self) -> None:
        self.run_route_script()


if __name__ == "__main__":
    unittest.main()
