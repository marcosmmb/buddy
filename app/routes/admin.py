from __future__ import annotations

from typing import Annotated, Any

from litestar import Controller, Request, delete, post
from litestar.exceptions import HTTPException
from litestar.params import Body
from sqlalchemy.exc import IntegrityError

from app.db import db_session
from app.models import SessionToken, TrackerMember, User
from app.schemas import AdminUserCreatePayload
from app.security import hash_password
from app.services import serialize_user
from app.utils import normalize_currency, require_admin, require_user


class AdminUserController(Controller):
    path = "/api/admin/users"

    @post()
    def create_user(self, request: Request, data: Annotated[AdminUserCreatePayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        require_admin(user)
        with db_session() as session:
            new_user = User(
                email=data.email,
                name=data.name.strip(),
                password_hash=hash_password(data.password),
                default_currency=normalize_currency(data.default_currency),
                is_admin=data.is_admin,
                is_active=True,
            )
            session.add(new_user)
            try:
                session.flush()
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
            return serialize_user(new_user)

    @delete("/{user_id:int}", status_code=200)
    def delete_user(self, request: Request, user_id: int) -> dict[str, str]:
        user = require_user(request)
        require_admin(user)
        if user.id == user_id:
            raise HTTPException(status_code=400, detail="You cannot delete your own account")
        with db_session() as session:
            db_user = session.get(User, user_id)
            if db_user is None or not db_user.is_active:
                raise HTTPException(status_code=404, detail="User not found")
            db_user.is_active = False
            session.query(SessionToken).filter(SessionToken.user_id == user_id).delete()
            session.query(TrackerMember).filter(TrackerMember.user_id == user_id).delete()
        return {"status": "ok"}
