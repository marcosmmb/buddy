from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BankingRouteTests(unittest.TestCase):
    def test_ignore_route_authentication_ownership_and_persistence(self) -> None:
        script = """
from decimal import Decimal

from litestar.testing import TestClient
from app.db import db_session
from app.main import app
from app.models import BankAccount, BankConnection, BankTransaction, Category, Expense, SessionToken, Tracker, TrackerMember, User, utcnow

with TestClient(app=app) as client:
    with db_session() as session:
        owner = User(email="owner@example.test", name="Owner", password_hash="x")
        member = User(email="member@example.test", name="Member", password_hash="x")
        admin = session.query(User).filter(User.is_admin.is_(True)).one()
        session.add_all([owner, member])
        session.flush()
        tracker = Tracker(name="Home", default_currency="CAD", created_by_id=owner.id)
        other_tracker = Tracker(name="Other", default_currency="CAD", created_by_id=owner.id)
        session.add_all([tracker, other_tracker])
        session.flush()
        session.add_all([
            TrackerMember(tracker_id=tracker.id, user_id=owner.id, role="owner", share_percent=Decimal("50")),
            TrackerMember(tracker_id=tracker.id, user_id=member.id, role="member", share_percent=Decimal("50")),
            TrackerMember(tracker_id=other_tracker.id, user_id=owner.id, role="owner", share_percent=Decimal("100")),
            SessionToken(token="owner-token", user_id=owner.id),
            SessionToken(token="member-token", user_id=member.id),
            SessionToken(token="admin-token", user_id=admin.id),
        ])
        category = Category(tracker_id=tracker.id, name="Groceries", color="#f1b84b")
        connection = BankConnection(tracker_id=tracker.id, user_id=owner.id, provider_item_id="item-test", institution_name="Test Bank", encrypted_access_token="unused")
        session.add_all([category, connection])
        session.flush()
        account = BankAccount(bank_connection_id=connection.id, provider_account_id="account-test", name="Checking")
        session.add(account)
        session.flush()
        transaction = BankTransaction(bank_account_id=account.id, provider_transaction_id="txn-test", date=utcnow().date(), amount=Decimal("42.50"), name="Market")
        session.add(transaction)
        session.flush()
        tracker_id, other_tracker_id = tracker.id, other_tracker.id
        transaction_id, category_id = transaction.id, category.id

    base_url = f"/api/trackers/{tracker_id}/bank/transactions"
    ignore_url = f"{base_url}/{transaction_id}/ignore"
    owner_headers = {"authorization": "Bearer owner-token"}
    response = client.post(ignore_url)
    assert response.status_code == 401, response.text
    for token in ["member-token", "admin-token"]:
        response = client.post(ignore_url, headers={"authorization": "Bearer " + token})
        assert response.status_code == 404, response.text
    response = client.post(f"/api/trackers/{other_tracker_id}/bank/transactions/{transaction_id}/ignore", headers=owner_headers)
    assert response.status_code == 404, response.text
    response = client.get(base_url, headers=owner_headers)
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [transaction_id], response.text

    for _ in range(2):
        response = client.post(ignore_url, headers=owner_headers)
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "ok"}, response.text
    response = client.get(base_url, headers=owner_headers)
    assert response.status_code == 200, response.text
    assert response.json() == [], response.text
    response = client.post(f"{base_url}/import", headers=owner_headers, json={"transactions": [{"transaction_id": transaction_id, "category_id": category_id}]})
    assert response.status_code == 201, response.text
    assert response.json() == {"imported": 0, "skipped": [{"transaction_id": transaction_id, "reason": "Transaction was ignored"}]}, response.text
    with db_session() as session:
        assert session.get(BankTransaction, transaction_id).ignored_at is not None
        assert session.query(Expense).count() == 0

    ignored_url = base_url + "/ignored?days=8"
    response = client.get(ignored_url, headers=owner_headers)
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [transaction_id], response.text
    assert client.get(ignored_url).status_code == 401
    restore_url = f"{base_url}/{transaction_id}/restore"
    assert client.post(restore_url).status_code == 401
    for token in ["member-token", "admin-token"]:
        headers = {"authorization": "Bearer " + token}
        response = client.get(ignored_url, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json() == [], response.text
        response = client.post(restore_url, headers=headers)
        assert response.status_code == 404, response.text
    response = client.post(f"/api/trackers/{other_tracker_id}/bank/transactions/{transaction_id}/restore", headers=owner_headers)
    assert response.status_code == 404, response.text
    response = client.post(f"{base_url}/999999/restore", headers=owner_headers)
    assert response.status_code == 404, response.text
    for _ in range(2):
        response = client.post(restore_url, headers=owner_headers)
        assert response.status_code == 200, response.text
    response = client.get(ignored_url, headers=owner_headers)
    assert response.status_code == 200 and response.json() == [], response.text
    response = client.get(base_url, headers=owner_headers)
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [transaction_id], response.text
    response = client.post(f"{base_url}/import", headers=owner_headers, json={"transactions": [{"transaction_id": transaction_id, "category_id": category_id, "is_shared": True}]})
    assert response.status_code == 201, response.text
    assert response.json() == {"imported": 1, "skipped": []}, response.text
    with db_session() as session:
        assert session.get(BankTransaction, transaction_id).ignored_at is None
        assert session.query(Expense).one().is_shared is True
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", script],
                env={
                    **os.environ,
                    "DATABASE_URL": f"sqlite:///{Path(directory) / 'buddy.sqlite3'}",
                    "ADMIN_EMAIL": "admin@buddy.local",
                    "ADMIN_PASSWORD": "change-me-now",
                    "LITESTAR_WARN_IMPLICIT_SYNC_TO_THREAD": "0",
                },
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
