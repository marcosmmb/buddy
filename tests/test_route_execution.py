from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RouteExecutionTests(unittest.TestCase):
    def test_startup_is_warning_free_and_bank_sync_does_not_block_other_requests(self) -> None:
        script = """
import warnings
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

with warnings.catch_warnings(record=True) as startup_warnings:
    warnings.simplefilter("always")
    from app.main import app
assert not [warning for warning in startup_warnings if "sync_to_thread" in str(warning.message)], startup_warnings

from litestar.testing import TestClient
from app.banking.crypto import encrypt_token
from app.db import db_session
from app.models import BankConnection, SessionToken, Tracker, TrackerMember, User

started, release = Event(), Event()

def slow_sync(*args):
    started.set()
    assert release.wait(timeout=10), "Test failed to release bank sync"
    return {"added": [], "modified": [], "removed": [], "next_cursor": "next", "has_more": False}

with TestClient(app=app) as client:
    with db_session() as session:
        user = session.query(User).filter(User.is_admin.is_(True)).one()
        tracker = Tracker(name="Home", created_by_id=user.id)
        session.add(tracker)
        session.flush()
        session.add_all([
            TrackerMember(tracker_id=tracker.id, user_id=user.id, role="owner"),
            SessionToken(token="owner-token", user_id=user.id),
        ])
        connection = BankConnection(
            tracker_id=tracker.id, user_id=user.id, provider_item_id="item-test",
            encrypted_access_token=encrypt_token("access-test"),
        )
        session.add(connection)
        session.flush()
        sync_url = f"/api/trackers/{tracker.id}/bank/connections/{connection.id}/sync"

    with patch("app.banking.service.PlaidClient") as plaid, ThreadPoolExecutor(max_workers=2) as pool:
        plaid.return_value.sync_transactions.side_effect = slow_sync
        bank_request = pool.submit(client.post, sync_url, headers={"authorization": "Bearer owner-token"})
        try:
            assert started.wait(timeout=5), "Bank sync did not start"
            currencies_request = pool.submit(client.get, "/api/currencies")
            response = currencies_request.result(timeout=5)
            assert response.status_code == 200, response.text
            assert "CAD" in response.json(), response.text
            assert not bank_request.done(), "Bank sync must still be waiting"
        finally:
            release.set()
        response = bank_request.result(timeout=5)
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "ok", response.text
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", script],
                env={
                    **os.environ,
                    "DATABASE_URL": f"sqlite:///{Path(directory) / 'buddy.sqlite3'}",
                    "ADMIN_EMAIL": "admin@buddy.local",
                    "ADMIN_PASSWORD": "change-me-now",
                    "LITESTAR_WARN_IMPLICIT_SYNC_TO_THREAD": "1",
                },
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
