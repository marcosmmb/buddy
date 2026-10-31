from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class UxRouteTests(unittest.TestCase):
    def test_undo_authorization_links_expiration_and_bank_health(self) -> None:
        script = r'''
from datetime import timedelta
from unittest.mock import patch
import httpx
from litestar.testing import TestClient
from app.main import app
from app.db import db_session
from app.banking.crypto import encrypt_token
from app.models import BankAccount, BankConnection, BankTransaction, Category, Expense, ExpenseDeletion, SessionToken, Tracker, TrackerMember, User, utcnow

with TestClient(app=app) as client:
    with db_session() as session:
        owner = User(email="owner@example.test", name="Owner", password_hash="x")
        other = User(email="other@example.test", name="Other", password_hash="x")
        session.add_all([owner, other]); session.flush()
        tracker = Tracker(name="Home", created_by_id=owner.id, default_currency="CAD")
        unrelated = Tracker(name="Private", created_by_id=other.id)
        session.add_all([tracker, unrelated]); session.flush()
        session.add_all([TrackerMember(tracker_id=tracker.id, user_id=owner.id, role="owner"), TrackerMember(tracker_id=tracker.id, user_id=other.id), SessionToken(user_id=owner.id, token="owner"), SessionToken(user_id=other.id, token="other")])
        category = Category(tracker_id=tracker.id, name="Food")
        session.add(category); session.flush()
        expense = Expense(tracker_id=tracker.id, category_id=category.id, paid_by_id=owner.id, date=utcnow().date(), amount="12.345", currency="CAD", description="Lunch")
        connection = BankConnection(tracker_id=tracker.id, user_id=owner.id, provider_item_id="item", encrypted_access_token=encrypt_token("secret-access"))
        session.add_all([expense, connection]); session.flush()
        account = BankAccount(bank_connection_id=connection.id, provider_account_id="account", name="Checking")
        session.add(account); session.flush()
        transaction = BankTransaction(bank_account_id=account.id, provider_transaction_id="txn", date=utcnow().date(), amount="12.345", expense_id=expense.id, status="imported")
        session.add(transaction); session.flush()
        tid, oid, cid, eid, bid, xid = tracker.id, other.id, category.id, expense.id, connection.id, transaction.id
        private_id = unrelated.id
        owner_id = owner.id
    owner_headers = {"authorization": "Bearer owner"}
    other_headers = {"authorization": "Bearer other"}
    base = f"/api/trackers/{tid}/expenses"
    assert client.post(base + "/bulk-delete", json={"expense_ids": [eid]}).status_code == 401
    deleted = client.post(base + "/bulk-delete", json={"expense_ids": [eid]}, headers=owner_headers)
    assert deleted.status_code == 200, deleted.text
    token = deleted.json()["undo_token"]
    undo = base + "/undo/" + token
    assert client.post(undo, headers=other_headers).status_code == 404
    assert client.post(f"/api/trackers/{private_id}/expenses/undo/{token}", headers=owner_headers).status_code == 404
    with db_session() as session:
        assert session.get(Expense, eid) is None
        assert session.get(BankTransaction, xid).expense_id is None
    # Reusing a deleted primary key must not attach the old bank transaction to a new expense.
    new = client.post(base, headers=owner_headers, json={"date": utcnow().date().isoformat(), "category_id": cid, "paid_by_id": owner_id, "amount": "8", "description": "New entry", "is_shared": True})
    assert new.status_code == 201, new.text
    restored = client.post(undo, headers=owner_headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] == 1
    assert client.post(undo, headers=owner_headers).status_code == 404
    with db_session() as session:
        linked = session.get(BankTransaction, xid).expense
        assert linked.description == "Lunch"
        assert str(linked.amount) == "12.345"
        assert linked.id != new.json()["id"]
        linked_id = linked.id
    deleted = client.delete(base + f"/{linked_id}", headers=owner_headers).json()
    with db_session() as session:
        session.get(ExpenseDeletion, deleted["undo_token"]).expires_at = utcnow() - timedelta(seconds=1)
    assert client.post(base + "/undo/" + deleted["undo_token"], headers=owner_headers).status_code == 404
    # Missing categories fail atomically and leave the undo token available.
    deleted = client.delete(base + f'/{new.json()["id"]}', headers=owner_headers).json()
    with db_session() as session:
        session.query(Category).filter(Category.id == cid).delete()
    assert client.post(base + "/undo/" + deleted["undo_token"], headers=owner_headers).status_code == 409
    with db_session() as session:
        assert session.get(ExpenseDeletion, deleted["undo_token"]) is not None
        assert session.query(Expense).count() == 0
    health_url = f"/api/trackers/{tid}/bank/connections/{bid}/health"
    with patch("app.routes.banking.PlaidClient") as plaid:
        assert client.get(health_url, headers=other_headers).status_code == 404
        plaid.assert_not_called()
        plaid.return_value.request.return_value = {"status": {"transactions": {"last_successful_update": "2026-10-22T14:00:00Z", "last_failed_update": "2026-10-31T09:00:00Z"}}, "item": {"error": {"error_code": "ITEM_LOGIN_REQUIRED"}, "access_token": "do-not-return"}}
        response = client.get(health_url, headers=owner_headers)
        assert response.status_code == 200, response.text
        assert response.json()["last_successful_update"] == "2026-10-22T14:00:00Z"
        assert response.json()["error_code"] == "ITEM_LOGIN_REQUIRED"
        assert "do-not-return" not in response.text
        plaid.return_value.request.side_effect = httpx.ReadTimeout("secret-details")
        response = client.get(health_url, headers=owner_headers)
        assert response.status_code == 502 and "secret-details" not in response.text
'''
        with tempfile.TemporaryDirectory() as directory:
            env = {**os.environ, "DATABASE_URL": f"sqlite:///{directory}/test.sqlite3", "LITESTAR_WARN_IMPLICIT_SYNC_TO_THREAD": "0"}
            result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
