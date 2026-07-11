from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://buddy:buddy@localhost:5432/buddy",
    )
    admin_email: str = os.getenv("ADMIN_EMAIL", "admin@buddy.local")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me-now")
    admin_name: str = os.getenv("ADMIN_NAME", "Buddy Admin")
    app_secret: str = os.getenv("APP_SECRET", "dev-secret-change-me")


settings = Settings()
