from __future__ import annotations

from typing import Any

from litestar import Controller, Request, get

from app.db import db_session
from app.models import User
from app.services import SUPPORTED_CURRENCIES, serialize_user
from app.utils import require_user


class UserController(Controller):
    path = "/api"

    @get("/currencies")
    def currencies(self) -> list[str]:
        return SUPPORTED_CURRENCIES

    @get("/users")
    def users(self, request: Request) -> list[dict[str, Any]]:
        require_user(request)
        with db_session() as session:
            return [serialize_user(user) for user in session.query(User).filter(User.is_active.is_(True)).order_by(User.name).all()]
