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
            "/api/admin/users",
            headers=headers,
            json={
                "name": "Sam",
                "email": "sam@example.test",
                "password": "password123",
                "default_currency": "USD",
            },
        )
        user_response.raise_for_status()
        sam_id = user_response.json()["id"]

        tracker_response = client.post(
            "/api/trackers",
            headers=headers,
            json={"name": "Home", "default_currency": "USD", "member_ids": [sam_id]},
        )
        tracker_response.raise_for_status()
        tracker = tracker_response.json()
        tracker_id = tracker["id"]
        admin_id = next(member["user_id"] for member in tracker["members"] if member["email"] == "admin@buddy.local")

        categories_response = client.get(f"/api/trackers/{tracker_id}/categories", headers=headers)
        categories_response.raise_for_status()
        seeded_categories = categories_response.json()
        assert len(seeded_categories) >= 5
        category_id = seeded_categories[0]["id"]

        category_response = client.post(
            f"/api/trackers/{tracker_id}/categories",
            headers=headers,
            json={"name": "Pets", "color": "#f1b84b"},
        )
        category_response.raise_for_status()

        expense_response = client.post(
            f"/api/trackers/{tracker_id}/expenses",
            headers=headers,
            json={
                "date": "2026-07-10",
                "category_id": category_id,
                "amount": "120.00",
                "paid_by_id": admin_id,
                "description": "Market run",
                "is_shared": True,
            },
        )
        expense_response.raise_for_status()
        expense_id = expense_response.json()["id"]

        update_response = client.put(
            f"/api/trackers/{tracker_id}/expenses/{expense_id}",
            headers=headers,
            json={
                "date": "2026-07-10",
                "category_id": category_id,
                "amount": "121.25",
                "paid_by_id": admin_id,
                "description": "Market run updated",
                "is_shared": False,
            },
        )
        update_response.raise_for_status()
        assert update_response.json()["amount"] == 121.25

        bad_share_response = client.put(
            f"/api/trackers/{tracker_id}/members",
            headers=headers,
            json={
                "members": [
                    {"user_id": admin_id, "role": "owner", "share_percent": 80},
                    {"user_id": sam_id, "role": "member", "share_percent": 40},
                ]
            },
        )
        assert bad_share_response.status_code == 400

        monthly_share_response = client.put(
            f"/api/trackers/{tracker_id}/monthly-shares",
            headers=headers,
            json={
                "month": "2026-07",
                "shares": [
                    {"user_id": admin_id, "share_percent": 65},
                    {"user_id": sam_id, "share_percent": 35},
                ],
            },
        )
        monthly_share_response.raise_for_status()
        assert monthly_share_response.json()["shares"][0]["share_percent"] == 65.0

        config_response = client.post(
            f"/api/trackers/{tracker_id}/csv-configs",
            headers=headers,
            json={
                "name": "Sample bank",
                "field_map": {"date": "Date", "description": "Description", "amount": "Amount"},
                "invert_amount": False,
            },
        )
        config_response.raise_for_status()
        config_id = config_response.json()["id"]

        preview_response = client.post(
            f"/api/trackers/{tracker_id}/csv-imports/preview",
            headers=headers,
            json={
                "config_id": config_id,
                "csv_text": 'Date,Description,Amount\n"2026-07-11","Coffee","5.25"\n',
                "fallback_category_id": category_id,
                "fallback_paid_by_id": admin_id,
            },
        )
        preview_response.raise_for_status()
        preview = preview_response.json()
        assert preview["rows"][0]["is_shared"] is False

        import_response = client.post(
            f"/api/trackers/{tracker_id}/csv-imports",
            headers=headers,
            json={
                "expenses": [
                    {
                        "date": preview["rows"][0]["date"],
                        "category_id": preview["rows"][0]["category_id"],
                        "amount": str(preview["rows"][0]["amount"]),
                        "paid_by_id": preview["rows"][0]["paid_by_id"],
                        "description": preview["rows"][0]["description"],
                        "is_shared": preview["rows"][0]["is_shared"],
                    }
                ]
            },
        )
        import_response.raise_for_status()
        assert import_response.json()["imported"] == 1

        delete_response = client.delete(f"/api/trackers/{tracker_id}/expenses/{expense_id}", headers=headers)
        delete_response.raise_for_status()

        extra_expense_response = client.post(
            f"/api/trackers/{tracker_id}/expenses",
            headers=headers,
            json={
                "date": "2026-07-12",
                "category_id": category_id,
                "amount": "9.00",
                "paid_by_id": admin_id,
                "description": "Bulk delete me",
                "is_shared": False,
            },
        )
        extra_expense_response.raise_for_status()
        bulk_response = client.post(
            f"/api/trackers/{tracker_id}/expenses/bulk-delete",
            headers=headers,
            json={"expense_ids": [extra_expense_response.json()["id"]]},
        )
        bulk_response.raise_for_status()
        assert bulk_response.json()["deleted"] == 1

        settlement_expense_response = client.post(
            f"/api/trackers/{tracker_id}/expenses",
            headers=headers,
            json={
                "date": "2026-07-13",
                "category_id": category_id,
                "amount": "200.00",
                "paid_by_id": admin_id,
                "description": "Shared settlement test",
                "is_shared": True,
            },
        )
        settlement_expense_response.raise_for_status()

        overview_response = client.get(
            f"/api/trackers/{tracker_id}/overview?period_type=month&period=2026-07",
            headers=headers,
        )
        overview_response.raise_for_status()
        settlements = overview_response.json()["balance"]["settlements"]
        assert settlements == [
            {
                "from_user_id": sam_id,
                "from": "Sam",
                "to_user_id": admin_id,
                "to": "Buddy Admin",
                "amount": 70.0,
            }
        ]

        for path in (
            f"/api/trackers/{tracker_id}/period-options",
            f"/api/trackers/{tracker_id}/monthly-shares?month=2026-07",
            f"/api/trackers/{tracker_id}/overview?period_type=month&period=2026-07",
            f"/api/trackers/{tracker_id}/overview?period_type=year&period=2026",
        ):
            response = client.get(path, headers=headers)
            response.raise_for_status()

    os.unlink(db_file.name)
    print("smoke ok")


if __name__ == "__main__":
    main()
