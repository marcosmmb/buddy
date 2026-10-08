from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BankingRouteTests(unittest.TestCase):
    def test_bank_reauthentication_permissions_failure_persistence_and_recovery(self) -> None:
        script = """
from unittest.mock import patch

from litestar.testing import TestClient
from app.banking.crypto import encrypt_token
from app.banking.plaid import PlaidApiError
from app.db import db_session
from app.main import app
from app.models import BankAccount, BankConnection, BankTransaction, SessionToken, Tracker, TrackerMember, User, utcnow
from app.two_factor import encrypt_totp_secret, generate_totp_secret, totp_code

with TestClient(app=app) as client:
    with db_session() as session:
        admin = session.query(User).filter(User.is_admin.is_(True)).one()
        secret = generate_totp_secret()
        user = User(email="owner@example.test", name="Owner", password_hash="x", two_factor_enabled=True, two_factor_secret=encrypt_totp_secret(secret))
        member = User(email="member@example.test", name="Member", password_hash="x")
        session.add_all([user, member])
        session.flush()
        tracker = Tracker(name="Home", created_by_id=user.id)
        session.add(tracker)
        session.flush()
        session.add_all([
            TrackerMember(tracker_id=tracker.id, user_id=user.id, role="owner"),
            TrackerMember(tracker_id=tracker.id, user_id=member.id, role="member"),
            SessionToken(token="owner-token", user_id=user.id),
            SessionToken(token="member-token", user_id=member.id),
            SessionToken(token="admin-token", user_id=admin.id),
        ])
        connection = BankConnection(
            tracker_id=tracker.id, user_id=user.id, provider_item_id="item-test",
            encrypted_access_token=encrypt_token("access-test"), sync_cursor="original-cursor",
        )
        session.add(connection)
        session.flush()
        account = BankAccount(bank_connection_id=connection.id, provider_account_id="account-test", name="Checking")
        session.add(account)
        session.flush()
        ignored_at = utcnow()
        transaction = BankTransaction(bank_account_id=account.id, provider_transaction_id="existing-transaction", date=utcnow().date(), amount=42, ignored_at=ignored_at)
        session.add(transaction)
        session.flush()
        tracker_id, connection_id, user_id, transaction_id = tracker.id, connection.id, user.id, transaction.id
        encrypted_access_token = connection.encrypted_access_token

    base_url = f"/api/trackers/{tracker_id}/bank"
    owner_headers = {"authorization": "Bearer owner-token"}
    error = PlaidApiError(
        "Bank login needs updating", "ITEM_LOGIN_REQUIRED",
        error_type="ITEM_ERROR", request_id="request-test", status_code=400,
    )
    with patch("app.banking.service.PlaidClient") as plaid:
        # A failure on a later page must also discard already-staged transactions.
        plaid.return_value.sync_transactions.side_effect = [{
            "added": [{"transaction_id": "partial-transaction", "account_id": "account-test", "amount": 10}],
            "next_cursor": "partial-cursor", "has_more": True,
        }, error]
        response = client.post(
            f"/api/trackers/{tracker_id}/bank/connections/{connection_id}/sync?days=15",
            headers={"authorization": "Bearer owner-token"},
        )
        assert plaid.return_value.sync_transactions.call_args_list[0].args == ("access-test", "original-cursor")
        assert plaid.return_value.sync_transactions.call_args_list[1].args == ("access-test", "partial-cursor")
    assert response.status_code == 409, response.text
    assert "Reconnect" in response.json()["detail"], response.text
    assert response.json()["extra"] == {
        "error_code": "ITEM_LOGIN_REQUIRED", "error_type": "ITEM_ERROR",
        "request_id": "request-test", "upstream_status_code": 400,
    }, response.text
    assert "access-test" not in response.text
    with db_session() as session:
        connection = session.get(BankConnection, connection_id)
        assert connection.sync_cursor == "original-cursor"
        assert connection.last_synced_at is None
        assert connection.status == "reauth_required"
        assert "Reconnect" in connection.error_message
        assert session.query(BankTransaction).filter_by(provider_transaction_id="partial-transaction").count() == 0
        assert session.get(BankTransaction, transaction_id).ignored_at is not None
    response = client.get(base_url + "/connections", headers=owner_headers)
    assert response.json()[0]["status"] == "reauth_required", response.text

    reconnect_url = f"{base_url}/connections/{connection_id}/link-token"
    with patch("app.routes.banking.PlaidClient") as plaid:
        plaid.return_value.create_update_link_token.return_value = "link-update"
        assert client.post(reconnect_url).status_code == 401
        for token in ["member-token", "admin-token"]:
            response = client.post(reconnect_url, headers={"authorization": "Bearer " + token}, json={"two_factor_code": totp_code(secret)})
            assert response.status_code == 404, response.text
        assert client.post(reconnect_url, headers=owner_headers).status_code == 401
        invalid_code = str((int(totp_code(secret)) + 1) % 1000000).zfill(6)
        with patch("app.routes.banking.verify_user_totp", return_value=False):
            assert client.post(reconnect_url, headers=owner_headers, json={"two_factor_code": invalid_code}).status_code == 401
        with db_session() as session:
            session.get(User, user_id).two_factor_enabled = False
        assert client.post(reconnect_url, headers=owner_headers, json={"two_factor_code": totp_code(secret)}).status_code == 403
        with db_session() as session:
            session.get(User, user_id).two_factor_enabled = True
        plaid.return_value.create_update_link_token.assert_not_called()
        response = client.post(reconnect_url, headers=owner_headers, json={"two_factor_code": totp_code(secret)})
        assert response.status_code == 201, response.text
        assert response.json() == {"link_token": "link-update"}, response.text
        assert plaid.return_value.create_update_link_token.call_args.args[0].id == user_id
        assert plaid.return_value.create_update_link_token.call_args.args[1] == "access-test"
        plaid.return_value.exchange_public_token.assert_not_called()
    with db_session() as session:
        assert session.get(BankConnection, connection_id).status == "reauth_required"

    # Completing Link retries the existing sync rather than exchanging a new token.
    with patch("app.banking.service.PlaidClient") as plaid:
        plaid.return_value.sync_transactions.return_value = {"next_cursor": "recovered-cursor", "has_more": False}
        response = client.post(f"{base_url}/connections/{connection_id}/sync", headers=owner_headers)
        assert response.status_code == 201, response.text
        plaid.return_value.sync_transactions.assert_called_once_with("access-test", "original-cursor")
    with db_session() as session:
        connection = session.get(BankConnection, connection_id)
        assert session.query(BankConnection).count() == 1
        assert connection.status == "active"
        assert connection.error_message == ""
        assert connection.last_synced_at is not None
        assert connection.sync_cursor == "recovered-cursor"
        assert connection.encrypted_access_token == encrypted_access_token
        assert connection.provider_item_id == "item-test"
        assert session.get(BankTransaction, transaction_id).ignored_at is not None

    # Other Plaid errors retain 502 and do not request reauthentication.
    with patch("app.banking.service.PlaidClient") as plaid:
        plaid.return_value.sync_transactions.side_effect = PlaidApiError("Bank unavailable", "INSTITUTION_DOWN", status_code=400)
        response = client.post(f"{base_url}/connections/{connection_id}/sync", headers=owner_headers)
        assert response.status_code == 502, response.text
        assert response.json()["extra"]["error_code"] == "INSTITUTION_DOWN", response.text
    with db_session() as session:
        connection = session.get(BankConnection, connection_id)
        assert connection.status == "error"
        assert connection.error_message == "Bank unavailable"
        assert connection.sync_cursor == "recovered-cursor"
        assert connection.last_synced_at is not None
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
