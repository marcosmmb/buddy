from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

from litestar import Litestar, Request, delete, get, post, put
from litestar.exceptions import HTTPException
from litestar.params import Body
from litestar.response import Response
from litestar.static_files.config import StaticFilesConfig
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import joinedload

from app.db import db_session, init_database
from app.models import Category, Expense, SessionToken, Tracker, TrackerMember, User
from app.schemas import (
    CategoryCreatePayload,
    ExpenseCreatePayload,
    LoginPayload,
    MemberUpdatePayload,
    PreferencesPayload,
    RegisterPayload,
    TrackerCreatePayload,
)
from app.security import hash_password, new_token, verify_password
from app.services import (
    balance_for_tracker,
    current_year,
    expense_query,
    get_tracker_for_user,
    overview_for_expenses,
    serialize_category,
    serialize_expense,
    serialize_tracker,
    serialize_user,
)


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def require_user(request: Request) -> User:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()
    with db_session() as session:
        session_token = (
            session.query(SessionToken)
            .options(joinedload(SessionToken.user))
            .filter(SessionToken.token == token)
            .one_or_none()
        )
        if session_token is None:
            raise HTTPException(status_code=401, detail="Invalid session")
        session.expunge(session_token.user)
        return session_token.user


def require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def normalize_currency(value: str) -> str:
    value = value.strip().upper()
    if len(value) != 3:
        raise HTTPException(status_code=400, detail="Currency must be a 3-letter code")
    return value


@get("/")
def index() -> Response[str]:
    return Response(content=(FRONTEND_DIR / "index.html").read_text(), media_type="text/html")


@post("/api/auth/login")
def login(data: Annotated[LoginPayload, Body()]) -> dict[str, Any]:
    with db_session() as session:
        user = session.query(User).filter(User.email == data.email.strip().lower()).one_or_none()
        if user is None or not verify_password(data.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        token = new_token()
        session.add(SessionToken(token=token, user_id=user.id))
        return {"token": token, "user": serialize_user(user)}


@post("/api/auth/register")
def register(data: Annotated[RegisterPayload, Body()]) -> dict[str, Any]:
    with db_session() as session:
        user = User(
            email=data.email,
            name=data.name.strip(),
            password_hash=hash_password(data.password),
            default_currency=normalize_currency(data.default_currency),
            is_admin=False,
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="A user with that email already exists") from exc
        token = new_token()
        session.add(SessionToken(token=token, user_id=user.id))
        return {"token": token, "user": serialize_user(user)}


@delete("/api/auth/logout", status_code=200)
def logout(request: Request) -> dict[str, str]:
    auth_header = request.headers.get("authorization", "")
    token = auth_header.split(" ", 1)[1].strip() if " " in auth_header else ""
    with db_session() as session:
        session.query(SessionToken).filter(SessionToken.token == token).delete()
    return {"status": "ok"}


@get("/api/me")
def me(request: Request) -> dict[str, Any]:
    return serialize_user(require_user(request))


@put("/api/me/preferences")
def update_preferences(request: Request, data: Annotated[PreferencesPayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        db_user = session.get(User, user.id)
        if db_user is None:
            raise HTTPException(status_code=404, detail="User not found")
        if data.name is not None:
            db_user.name = data.name.strip()
        if data.default_currency is not None:
            db_user.default_currency = normalize_currency(data.default_currency)
        session.flush()
        return serialize_user(db_user)


@get("/api/users")
def users(request: Request) -> list[dict[str, Any]]:
    require_user(request)
    with db_session() as session:
        return [serialize_user(user) for user in session.query(User).order_by(User.name).all()]


@get("/api/trackers")
def trackers(request: Request) -> list[dict[str, Any]]:
    user = require_user(request)
    with db_session() as session:
        query = session.query(Tracker).options(joinedload(Tracker.members).joinedload(TrackerMember.user))
        if not user.is_admin:
            query = query.join(TrackerMember).filter(TrackerMember.user_id == user.id)
        return [serialize_tracker(tracker) for tracker in query.order_by(Tracker.name).all()]


@post("/api/trackers")
def create_tracker(request: Request, data: Annotated[TrackerCreatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    require_admin(user)
    with db_session() as session:
        member_ids = sorted(set([user.id, *data.member_ids]))
        members = session.query(User).filter(User.id.in_(member_ids)).all()
        if len(members) != len(member_ids):
            raise HTTPException(status_code=400, detail="One or more members do not exist")
        tracker = Tracker(
            name=data.name.strip(),
            default_currency=normalize_currency(data.default_currency),
            created_by_id=user.id,
        )
        session.add(tracker)
        session.flush()
        share = Decimal("100") / Decimal(len(members)) if members else Decimal("0")
        for member in members:
            session.add(
                TrackerMember(
                    tracker_id=tracker.id,
                    user_id=member.id,
                    role="owner" if member.id == user.id else "member",
                    share_percent=share,
                )
            )
        session.flush()
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker.id)
            .one()
        )
        return serialize_tracker(tracker)


@put("/api/trackers/{tracker_id:int}/members")
def update_members(request: Request, tracker_id: int, data: Annotated[MemberUpdatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    require_admin(user)
    with db_session() as session:
        tracker = session.get(Tracker, tracker_id)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        payload_by_user: dict[int, dict[str, Any]] = {}
        for item in data.members:
            user_id = int(item["user_id"])
            payload_by_user[user_id] = item
        if not payload_by_user:
            raise HTTPException(status_code=400, detail="A tracker needs at least one member")
        existing_users = session.query(User).filter(User.id.in_(payload_by_user.keys())).all()
        if len(existing_users) != len(payload_by_user):
            raise HTTPException(status_code=400, detail="One or more members do not exist")
        session.query(TrackerMember).filter(TrackerMember.tracker_id == tracker_id).delete()
        for user_id, item in payload_by_user.items():
            session.add(
                TrackerMember(
                    tracker_id=tracker_id,
                    user_id=user_id,
                    role=str(item.get("role", "member")),
                    share_percent=Decimal(str(item.get("share_percent", 0))),
                )
            )
        session.flush()
        tracker = (
            session.query(Tracker)
            .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            .filter(Tracker.id == tracker_id)
            .one()
        )
        return serialize_tracker(tracker)


@get("/api/trackers/{tracker_id:int}/categories")
def categories(request: Request, tracker_id: int) -> list[dict[str, Any]]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        categories = session.query(Category).filter(Category.tracker_id == tracker_id).order_by(Category.name).all()
        return [serialize_category(category) for category in categories]


@post("/api/trackers/{tracker_id:int}/categories")
def create_category(request: Request, tracker_id: int, data: Annotated[CategoryCreatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        category = Category(tracker_id=tracker_id, name=data.name.strip(), color=data.color)
        session.add(category)
        try:
            session.flush()
        except IntegrityError as exc:
            raise HTTPException(status_code=409, detail="That category already exists") from exc
        return serialize_category(category)


@get("/api/trackers/{tracker_id:int}/expenses")
def expenses(request: Request, tracker_id: int, month: str | None = None, year: int | None = None) -> list[dict[str, Any]]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        rows = expense_query(session, tracker_id, month=month, year=year).all()
        return [serialize_expense(expense) for expense in rows]


@post("/api/trackers/{tracker_id:int}/expenses")
def create_expense(request: Request, tracker_id: int, data: Annotated[ExpenseCreatePayload, Body()]) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        member_ids = {member.user_id for member in tracker.members}
        if data.paid_by_id not in member_ids:
            raise HTTPException(status_code=400, detail="Payer must be a tracker member")
        category = session.get(Category, data.category_id)
        if category is None or category.tracker_id != tracker_id:
            raise HTTPException(status_code=400, detail="Category must belong to the tracker")
        expense = Expense(
            tracker_id=tracker_id,
            category_id=data.category_id,
            paid_by_id=data.paid_by_id,
            date=data.date,
            amount=data.amount,
            currency=normalize_currency(data.currency),
            description=data.description.strip(),
            is_shared=data.is_shared,
        )
        session.add(expense)
        session.flush()
        expense = (
            session.query(Expense)
            .options(joinedload(Expense.category), joinedload(Expense.paid_by))
            .filter(Expense.id == expense.id)
            .one()
        )
        return serialize_expense(expense)


@get("/api/trackers/{tracker_id:int}/overview")
def overview(request: Request, tracker_id: int, month: str | None = None, year: int | None = None) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        if get_tracker_for_user(session, tracker_id, user) is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        rows = expense_query(session, tracker_id, month=month, year=year).all()
        return overview_for_expenses(rows)


@get("/api/trackers/{tracker_id:int}/balance")
def balance(request: Request, tracker_id: int, month: str | None = None) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        rows = expense_query(session, tracker_id, month=month).all()
        return balance_for_tracker(tracker, rows)


@get("/api/trackers/{tracker_id:int}/ytd")
def ytd(request: Request, tracker_id: int, year: int | None = None) -> dict[str, Any]:
    user = require_user(request)
    with db_session() as session:
        tracker = get_tracker_for_user(session, tracker_id, user)
        if tracker is None:
            raise HTTPException(status_code=404, detail="Tracker not found")
        selected_year = year or current_year()
        rows = expense_query(session, tracker_id, year=selected_year).all()
        return {
            "year": selected_year,
            "overview": overview_for_expenses(rows),
            "balance": balance_for_tracker(tracker, rows),
        }


app = Litestar(
    route_handlers=[
        index,
        login,
        register,
        logout,
        me,
        update_preferences,
        users,
        trackers,
        create_tracker,
        update_members,
        categories,
        create_category,
        expenses,
        create_expense,
        overview,
        balance,
        ytd,
    ],
    on_startup=[init_database],
    static_files_config=[
        StaticFilesConfig(path="/static", directories=[FRONTEND_DIR / "static"]),
    ],
)
