from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "sqlite:///./buddy.sqlite3",
    )
    admin_email: str = os.getenv("ADMIN_EMAIL", "admin@buddy.local")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me-now")
    admin_name: str = os.getenv("ADMIN_NAME", "Buddy Admin")
    app_secret: str = os.getenv("APP_SECRET", "dev-secret-change-me")
    plaid_client_id: str = os.getenv("PLAID_CLIENT_ID", "")
    plaid_secret: str = os.getenv("PLAID_SECRET", "")
    plaid_env: str = os.getenv("PLAID_ENV", "sandbox")
    plaid_products: str = os.getenv("PLAID_PRODUCTS", "transactions")
    plaid_country_codes: str = os.getenv("PLAID_COUNTRY_CODES", "CA")
    bank_token_encryption_key: str = os.getenv("BANK_TOKEN_ENCRYPTION_KEY", "")

    @property
    def plaid_configured(self) -> bool:
        return bool(self.plaid_client_id and self.plaid_secret)


settings = Settings()
