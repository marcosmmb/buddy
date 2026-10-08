from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.models import Tracker, User


logger = logging.getLogger(__name__)

PLAID_BASE_URLS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        error_code: str = "",
        *,
        error_type: str = "",
        request_id: str = "",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.error_type = error_type
        self.request_id = request_id
        self.status_code = status_code


def plaid_base_url() -> str:
    return PLAID_BASE_URLS.get(settings.plaid_env, PLAID_BASE_URLS["sandbox"])


def plaid_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class PlaidClient:
    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self.http_client = http_client or httpx.Client(timeout=30)

    def request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not settings.plaid_configured:
            raise PlaidApiError("Plaid is not configured")
        response = self.http_client.post(
            f"{plaid_base_url()}{path}",
            json={
                "client_id": settings.plaid_client_id,
                "secret": settings.plaid_secret,
                **payload,
            },
        )
        data = response.json()
        if response.status_code >= 400:
            error = PlaidApiError(
                data.get("error_message") or "Plaid request failed",
                data.get("error_code") or "",
                error_type=data.get("error_type") or "",
                request_id=data.get("request_id") or "",
                status_code=response.status_code,
            )
            # Log diagnostic identifiers only; payloads and messages can contain credentials.
            logger.warning(
                "Plaid request failed: path=%s status=%s error_type=%s error_code=%s request_id=%s",
                path,
                error.status_code,
                error.error_type,
                error.error_code,
                error.request_id,
            )
            raise error
        return data

    def create_link_token(self, user: User, tracker: Tracker) -> str:
        payload: dict[str, Any] = {
            "client_name": "Buddy",
            "country_codes": plaid_list(settings.plaid_country_codes),
            "language": "en",
            "products": plaid_list(settings.plaid_products),
            "user": {"client_user_id": str(user.id)},
            "transactions": {"days_requested": 730},
        }
        return self.request("/link/token/create", payload)["link_token"]

    def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        return self.request("/item/public_token/exchange", {"public_token": public_token})

    def create_update_link_token(self, user: User, access_token: str) -> str:
        return self.request("/link/token/create", {
            "client_name": "Buddy",
            "country_codes": plaid_list(settings.plaid_country_codes),
            "language": "en",
            "user": {"client_user_id": str(user.id)},
            "access_token": access_token,
        })["link_token"]

    def get_accounts(self, access_token: str) -> list[dict[str, Any]]:
        return self.request("/accounts/get", {"access_token": access_token}).get("accounts", [])

    def sync_transactions(self, access_token: str, cursor: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"access_token": access_token, "count": 500}
        if cursor:
            payload["cursor"] = cursor
        return self.request("/transactions/sync", payload)
