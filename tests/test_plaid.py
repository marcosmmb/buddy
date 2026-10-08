from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from app.banking.plaid import PlaidApiError, PlaidClient
from app.config import Settings
from app.models import User


class PlaidClientTests(unittest.TestCase):
    def setUp(self) -> None:
        settings_patch = patch(
            "app.banking.plaid.settings",
            Settings(plaid_client_id="test-client", plaid_secret="test-secret", plaid_env="production"),
        )
        settings_patch.start()
        self.addCleanup(settings_patch.stop)

    def client_with_response(self, status: int, body: dict) -> PlaidClient:
        http_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))
        self.addCleanup(http_client.close)
        return PlaidClient(http_client)

    def test_sync_error_preserves_diagnostics_without_logging_credentials(self) -> None:
        client = self.client_with_response(400, {
            "error_message": "Sensitive provider message: access-private",
            "error_code": "ITEM_LOGIN_REQUIRED",
            "error_type": "ITEM_ERROR",
            "request_id": "request-test",
        })
        with self.assertLogs("app.banking.plaid", level="WARNING") as logs:
            with self.assertRaises(PlaidApiError) as caught:
                client.sync_transactions("access-private", "cursor-private")

        error = caught.exception
        self.assertEqual(str(error), "Sensitive provider message: access-private")
        self.assertEqual(error.error_code, "ITEM_LOGIN_REQUIRED")
        self.assertEqual(error.error_type, "ITEM_ERROR")
        self.assertEqual(error.request_id, "request-test")
        self.assertEqual(error.status_code, 400)
        output = "\n".join(logs.output)
        for expected in ["/transactions/sync", "status=400", "ITEM_LOGIN_REQUIRED", "ITEM_ERROR", "request-test"]:
            self.assertIn(expected, output)
        for credential in ["test-client", "test-secret", "access-private", "cursor-private", "Sensitive provider message"]:
            self.assertNotIn(credential, output)

    def test_error_without_diagnostics_uses_fallbacks(self) -> None:
        client = self.client_with_response(400, {"error_code": None, "error_type": None, "request_id": None})
        with self.assertLogs("app.banking.plaid", level="WARNING"):
            with self.assertRaises(PlaidApiError) as caught:
                client.sync_transactions("access-test")
        self.assertEqual(str(caught.exception), "Plaid request failed")
        self.assertEqual(caught.exception.error_code, "")
        self.assertEqual(caught.exception.error_type, "")
        self.assertEqual(caught.exception.request_id, "")

    def test_successful_sync_keeps_cursor_and_payload(self) -> None:
        response = {"added": [], "modified": [], "removed": [], "next_cursor": "next", "has_more": False}
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=response)

        with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
            client = PlaidClient(http_client)
            self.assertEqual(client.sync_transactions("access-test"), response)
            self.assertEqual(client.sync_transactions("access-test", "cursor-test"), response)
        self.assertNotIn("cursor", requests[0])
        self.assertEqual(requests[1]["cursor"], "cursor-test")
        self.assertEqual(requests[1]["count"], 500)

    def test_update_link_token_uses_existing_item_without_initial_link_products(self) -> None:
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/link/token/create")
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"link_token": "link-update"})

        with httpx.Client(transport=httpx.MockTransport(respond)) as http_client:
            token = PlaidClient(http_client).create_update_link_token(User(id=42), "existing-access-token")
        self.assertEqual(token, "link-update")
        self.assertEqual(requests[0]["access_token"], "existing-access-token")
        self.assertEqual(requests[0]["user"], {"client_user_id": "42"})
        self.assertNotIn("products", requests[0])
        self.assertNotIn("transactions", requests[0])


if __name__ == "__main__":
    unittest.main()
