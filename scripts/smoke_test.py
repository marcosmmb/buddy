from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    db_file = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    db_file.close()
    os.environ["DATABASE_URL"] = f"sqlite:///{db_file.name}"
    os.environ["ADMIN_EMAIL"] = "admin@buddy.local"
    os.environ["ADMIN_PASSWORD"] = "change-me-now"
    os.environ["ADMIN_NAME"] = "Buddy Admin"

    from litestar.testing import TestClient

    from app.main import app

    with TestClient(app=app) as client:
        login_response = client.post(
            "/api/auth/login",
            json={"email": "admin@buddy.local", "password": "change-me-now"},
        )
        login_response.raise_for_status()
        token = login_response.json()["token"]
        headers = {"authorization": f"Bearer {token}"}

        user_response = client.post(
            "/api/auth/register",
            json={
                "name": "Sam",
                "email": "sam@example.test",
                "password": "password123",
                "default_currency": "USD",
            },
        )
        user_response.raise_for_status()
        sam_id = user_response.json()["user"]["id"]

        tracker_response = client.post(
            "/api/trackers",
            headers=headers,
            json={"name": "Home", "default_currency": "USD", "member_ids": [sam_id]},
        )
        tracker_response.raise_for_status()
        tracker = tracker_response.json()
        tracker_id = tracker["id"]
        admin_id = next(member["user_id"] for member in tracker["members"] if member["email"] == "admin@buddy.local")

        category_response = client.post(
            f"/api/trackers/{tracker_id}/categories",
            headers=headers,
            json={"name": "Groceries", "color": "#166d5b"},
        )
        category_response.raise_for_status()
        category_id = category_response.json()["id"]

        expense_response = client.post(
            f"/api/trackers/{tracker_id}/expenses",
            headers=headers,
            json={
                "date": "2026-07-10",
                "category_id": category_id,
                "amount": "120.00",
                "currency": "USD",
                "paid_by_id": admin_id,
                "description": "Market run",
                "is_shared": True,
            },
        )
        expense_response.raise_for_status()

        for path in (
            f"/api/trackers/{tracker_id}/overview?month=2026-07",
            f"/api/trackers/{tracker_id}/balance?month=2026-07",
            f"/api/trackers/{tracker_id}/ytd?year=2026",
        ):
            response = client.get(path, headers=headers)
            response.raise_for_status()

    os.unlink(db_file.name)
    print("smoke ok")


if __name__ == "__main__":
    main()
