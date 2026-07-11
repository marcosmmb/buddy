from __future__ import annotations

from typing import Annotated, Any

from litestar import Controller, Request, delete, get, post
from litestar.exceptions import HTTPException
from litestar.params import Body
from sqlalchemy.exc import IntegrityError

from app.db import db_session
from app.models import Category, Expense
from app.schemas import CategoryCreatePayload
from app.services import get_tracker_for_user, is_tracker_owner, serialize_category
from app.utils import require_user


class CategoryController(Controller):
    path = "/api/trackers/{tracker_id:int}/categories"

    @get()
    def categories(self, request: Request, tracker_id: int) -> list[dict[str, Any]]:
        user = require_user(request)
        with db_session() as session:
            if get_tracker_for_user(session, tracker_id, user) is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            categories = session.query(Category).filter(Category.tracker_id == tracker_id).order_by(Category.name).all()
            return [serialize_category(category) for category in categories]

    @post()
    def create_category(self, request: Request, tracker_id: int, data: Annotated[CategoryCreatePayload, Body()]) -> dict[str, Any]:
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

    @delete("/{category_id:int}", status_code=200)
    def delete_category(self, request: Request, tracker_id: int, category_id: int) -> dict[str, str]:
        user = require_user(request)
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            if not is_tracker_owner(tracker, user):
                raise HTTPException(status_code=403, detail="Only tracker owners can manage categories")
            has_expenses = session.query(Expense).filter(Expense.category_id == category_id).first() is not None
            if has_expenses:
                raise HTTPException(status_code=400, detail="Cannot delete a category that has expenses")
            deleted = session.query(Category).filter(Category.id == category_id, Category.tracker_id == tracker_id).delete()
            if not deleted:
                raise HTTPException(status_code=404, detail="Category not found")
        return {"status": "ok"}
