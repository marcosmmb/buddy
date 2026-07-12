from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TwoFactorRouteTests(unittest.TestCase):
    def test_setup_route_is_registered_and_returns_setup_payload(self) -> None:
        db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        db_file.close()
        script = f"""
import os
import sys

os.environ["DATABASE_URL"] = "sqlite:///{db_file.name}"
os.environ["ADMIN_EMAIL"] = "admin@buddy.local"
os.environ["ADMIN_PASSWORD"] = "change-me-now"
os.environ["ADMIN_NAME"] = "Buddy Admin"
os.environ["LITESTAR_WARN_IMPLICIT_SYNC_TO_THREAD"] = "0"
sys.path.insert(0, {str(Path.cwd())!r})

from litestar.testing import TestClient
from app.main import app

with TestClient(app=app) as client:
    login_response = client.post("/api/auth/login", json={{"email": "admin@buddy.local", "password": "change-me-now"}})
    assert login_response.status_code == 201, login_response.text
    headers = {{"authorization": "Bearer " + login_response.json()["token"]}}

    setup_response = client.post("/api/me/2fa/setup", headers=headers, json={{"current_password": "change-me-now"}})
    assert setup_response.status_code == 201, setup_response.text
    setup_payload = setup_response.json()
    assert "secret" in setup_payload, setup_payload
    assert setup_payload["otpauth_uri"].startswith("otpauth://totp/"), setup_payload
    assert setup_payload["qr_svg"].startswith("<svg"), setup_payload
    assert "viewBox=" in setup_payload["qr_svg"], setup_payload

    bad_password_response = client.post("/api/me/2fa/setup", headers=headers, json={{"current_password": "wrong"}})
    assert bad_password_response.status_code == 400, bad_password_response.text
"""
        result = subprocess.run([sys.executable, "-c", script], check=False)
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
