from __future__ import annotations

from typing import Annotated, Any

from litestar import Controller, Request, delete, get, post, put
from litestar.exceptions import HTTPException
from litestar.params import Body
from sqlalchemy.exc import IntegrityError

from app.db import db_session
from app.models import SessionToken, User
from app.schemas import LoginPayload, PreferencesPayload, RegisterPayload
from app.security import hash_password, new_token, verify_password
from app.services import serialize_user
from app.utils import normalize_currency, require_user


class AuthController(Controller):
    path = "/api/auth"

    @post("/login")
    def login(self, data: Annotated[LoginPayload, Body()]) -> dict[str, Any]:
        with db_session() as session:
            user = session.query(User).filter(User.email == data.email.strip().lower()).one_or_none()
            if user is None or not user.is_active or not verify_password(data.password, user.password_hash):
                raise HTTPException(status_code=401, detail="Invalid email or password")
            token = new_token()
            session.add(SessionToken(token=token, user_id=user.id))
            return {"token": token, "user": serialize_user(user)}

    @post("/register")
    def register(self, data: Annotated[RegisterPayload, Body()]) -> dict[str, Any]:
        with db_session() as session:
            user = User(
                email=data.email,
                name=data.name.strip(),
                password_hash=hash_password(data.password),
                default_currency=normalize_currency(data.default_currency),
                is_admin=False,
                is_active=True,
            )
            session.add(user)
            try:
                session.flush()
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
            token = new_token()
            session.add(SessionToken(token=token, user_id=user.id))
            return {"token": token, "user": serialize_user(user)}

    @delete("/logout", status_code=200)
    def logout(self, request: Request) -> dict[str, str]:
        auth_header = request.headers.get("authorization", "")
        token = auth_header.split(" ", 1)[1].strip() if " " in auth_header else ""
        with db_session() as session:
            session.query(SessionToken).filter(SessionToken.token == token).delete()
        return {"status": "ok"}


class ProfileController(Controller):
    path = "/api/me"

    @get()
    def me(self, request: Request) -> dict[str, Any]:
        return serialize_user(require_user(request))

    @put("/preferences")
    def update_preferences(self, request: Request, data: Annotated[PreferencesPayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            db_user = session.get(User, user.id)
            if db_user is None:
                raise HTTPException(status_code=404, detail="User not found")
            if data.name is not None:
                db_user.name = data.name.strip()
            if data.default_currency is not None:
                db_user.default_currency = normalize_currency(data.default_currency)
            if data.theme is not None:
                if data.theme not in {"light", "dark"}:
                    raise HTTPException(status_code=400, detail="Theme must be light or dark")
                db_user.theme = data.theme
            if data.new_password:
                if not data.current_password or not verify_password(data.current_password, db_user.password_hash):
                    raise HTTPException(status_code=400, detail="Current password is incorrect")
                db_user.password_hash = hash_password(data.new_password)
            session.flush()
            return serialize_user(db_user)
