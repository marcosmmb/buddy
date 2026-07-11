from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from litestar import Controller, Request, delete, get, post, put
from litestar.exceptions import HTTPException
from litestar.params import Body
from sqlalchemy.orm import joinedload

from app.db import db_session
from app.models import Category, CsvImportConfig, Expense, Tracker, TrackerMember, TrackerMonthlyShare, User
from app.schemas import MemberUpdatePayload, MonthlySharesPayload, TrackerCreatePayload, TrackerUpdatePayload
from app.services import get_tracker_for_user, is_tracker_owner, serialize_tracker
from app.utils import (
    STARTER_CATEGORIES,
    monthly_share_response,
    normalize_currency,
    normalize_month,
    require_admin,
    require_user,
    validate_share_total,
)


class TrackerController(Controller):
    path = "/api/trackers"

    @get()
    def list_trackers(self, request: Request) -> list[dict[str, Any]]:
        user = require_user(request)
        with db_session() as session:
            query = session.query(Tracker).options(joinedload(Tracker.members).joinedload(TrackerMember.user))
            if not user.is_admin:
                query = query.join(TrackerMember).filter(TrackerMember.user_id == user.id)
            return [serialize_tracker(tracker) for tracker in query.order_by(Tracker.name).all()]

    @post()
    def create_tracker(self, request: Request, data: Annotated[TrackerCreatePayload, Body()]) -> dict[str, Any]:
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
            for name, color in STARTER_CATEGORIES:
                session.add(Category(tracker_id=tracker.id, name=name, color=color))
            session.flush()
            tracker = (
                session.query(Tracker)
                .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
                .filter(Tracker.id == tracker.id)
                .one()
            )
            return serialize_tracker(tracker)

    @put("/{tracker_id:int}")
    def update_tracker(self, request: Request, tracker_id: int, data: Annotated[TrackerUpdatePayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = (
                session.query(Tracker)
                .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
                .filter(Tracker.id == tracker_id)
                .one_or_none()
            )
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            if not is_tracker_owner(tracker, user):
                raise HTTPException(status_code=403, detail="Only tracker owners can update tracker settings")
            name = data.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="Tracker name is required")
            currency = normalize_currency(data.default_currency)
            tracker.name = name
            tracker.default_currency = currency
            session.query(Expense).filter(Expense.tracker_id == tracker_id).update({"currency": currency}, synchronize_session=False)
            session.query(CsvImportConfig).filter(CsvImportConfig.tracker_id == tracker_id).update({"currency": currency}, synchronize_session=False)
            session.flush()
            tracker = (
                session.query(Tracker)
                .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
                .filter(Tracker.id == tracker_id)
                .one()
            )
            return serialize_tracker(tracker)

    @delete("/{tracker_id:int}", status_code=200)
    def delete_tracker(self, request: Request, tracker_id: int) -> dict[str, str]:
        user = require_user(request)
        with db_session() as session:
            tracker = (
                session.query(Tracker)
                .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
                .filter(Tracker.id == tracker_id)
                .one_or_none()
            )
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            if not is_tracker_owner(tracker, user):
                raise HTTPException(status_code=403, detail="Only tracker owners can delete trackers")
            session.delete(tracker)
        return {"status": "ok"}

    @put("/{tracker_id:int}/members")
    def update_members(self, request: Request, tracker_id: int, data: Annotated[MemberUpdatePayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = (
                session.query(Tracker)
                .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
                .filter(Tracker.id == tracker_id)
                .one_or_none()
            )
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            if not is_tracker_owner(tracker, user):
                raise HTTPException(status_code=403, detail="Only tracker owners can manage members")
            validate_share_total(data.members)
            payload_by_user: dict[int, dict[str, Any]] = {}
            for item in data.members:
                user_id = int(item["user_id"])
                payload_by_user[user_id] = item
            if not payload_by_user:
                raise HTTPException(status_code=400, detail="A tracker needs at least one member")
            existing_users = session.query(User).filter(User.id.in_(payload_by_user.keys())).all()
            if len(existing_users) != len(payload_by_user):
                raise HTTPException(status_code=400, detail="One or more members do not exist")
            if not any(str(item.get("role", "member")) == "owner" for item in payload_by_user.values()):
                raise HTTPException(status_code=400, detail="A tracker needs at least one owner")
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

    @get("/{tracker_id:int}/monthly-shares")
    def monthly_shares(self, request: Request, tracker_id: int, month: str) -> dict[str, Any]:
        user = require_user(request)
        selected_month = normalize_month(month)
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            return monthly_share_response(session, tracker, selected_month)

    @put("/{tracker_id:int}/monthly-shares")
    def update_monthly_shares(self, request: Request, tracker_id: int, data: Annotated[MonthlySharesPayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        selected_month = normalize_month(data.month)
        validate_share_total(data.shares)
        with db_session() as session:
            tracker = (
                session.query(Tracker)
                .options(joinedload(Tracker.members).joinedload(TrackerMember.user))
                .filter(Tracker.id == tracker_id)
                .one_or_none()
            )
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            if not is_tracker_owner(tracker, user):
                raise HTTPException(status_code=403, detail="Only tracker owners can manage monthly shares")
            member_ids = {member.user_id for member in tracker.members}
            for item in data.shares:
                user_id = int(item["user_id"])
                if user_id not in member_ids:
                    raise HTTPException(status_code=400, detail="Monthly shares can only be set for tracker members")
                share = (
                    session.query(TrackerMonthlyShare)
                    .filter(
                        TrackerMonthlyShare.tracker_id == tracker_id,
                        TrackerMonthlyShare.user_id == user_id,
                        TrackerMonthlyShare.month == selected_month,
                    )
                    .one_or_none()
                )
                if share is None:
                    share = TrackerMonthlyShare(tracker_id=tracker_id, user_id=user_id, month=selected_month)
                    session.add(share)
                share.share_percent = Decimal(str(item.get("share_percent", 0)))
            session.flush()
            return monthly_share_response(session, tracker, selected_month)
