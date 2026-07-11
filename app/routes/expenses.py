from __future__ import annotations

from typing import Annotated, Any

from litestar import Controller, Request, delete, get, post, put
from litestar.exceptions import HTTPException
from litestar.params import Body
from sqlalchemy.orm import joinedload

from app.db import db_session
from app.models import Expense
from app.schemas import ExpenseBulkDeletePayload, ExpenseCreatePayload
from app.services import expense_query, get_tracker_for_user, serialize_expense
from app.utils import load_tracker_member_context, require_user, validate_expense_payload


class ExpenseController(Controller):
    path = "/api/trackers/{tracker_id:int}/expenses"

    @get()
    def expenses(self, request: Request, tracker_id: int, month: str | None = None, year: int | None = None) -> list[dict[str, Any]]:
        user = require_user(request)
        with db_session() as session:
            if get_tracker_for_user(session, tracker_id, user) is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            rows = expense_query(session, tracker_id, month=month, year=year).all()
            return [serialize_expense(expense) for expense in rows]

    @post()
    def create_expense(self, request: Request, tracker_id: int, data: Annotated[ExpenseCreatePayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = load_tracker_member_context(session, tracker_id, user)
            validate_expense_payload(session, tracker, tracker_id, data)
            expense = Expense(
                tracker_id=tracker_id,
                category_id=data.category_id,
                paid_by_id=data.paid_by_id,
                date=data.date,
                amount=data.amount,
                currency=tracker.default_currency,
                description=data.description.strip(),
                is_shared=data.is_shared,
            )
            session.add(expense)
            session.flush()
            expense = (
                session.query(Expense)
                .options(joinedload(Expense.category), joinedload(Expense.paid_by), joinedload(Expense.tracker))
                .filter(Expense.id == expense.id)
                .one()
            )
            return serialize_expense(expense)

    @put("/{expense_id:int}")
    def update_expense(self, request: Request, tracker_id: int, expense_id: int, data: Annotated[ExpenseCreatePayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = load_tracker_member_context(session, tracker_id, user)
            validate_expense_payload(session, tracker, tracker_id, data)
            expense = session.query(Expense).filter(Expense.id == expense_id, Expense.tracker_id == tracker_id).one_or_none()
            if expense is None:
                raise HTTPException(status_code=404, detail="Expense not found")
            expense.category_id = data.category_id
            expense.paid_by_id = data.paid_by_id
            expense.date = data.date
            expense.amount = data.amount
            expense.currency = tracker.default_currency
            expense.description = data.description.strip()
            expense.is_shared = data.is_shared
            session.flush()
            expense = (
                session.query(Expense)
                .options(joinedload(Expense.category), joinedload(Expense.paid_by), joinedload(Expense.tracker))
                .filter(Expense.id == expense.id)
                .one()
            )
            return serialize_expense(expense)

    @delete("/{expense_id:int}", status_code=200)
    def delete_expense(self, request: Request, tracker_id: int, expense_id: int) -> dict[str, str]:
        user = require_user(request)
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            deleted = session.query(Expense).filter(Expense.id == expense_id, Expense.tracker_id == tracker_id).delete()
            if not deleted:
                raise HTTPException(status_code=404, detail="Expense not found")
        return {"status": "ok"}

    @post("/bulk-delete", status_code=200)
    def bulk_delete_expenses(self, request: Request, tracker_id: int, data: Annotated[ExpenseBulkDeletePayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            if get_tracker_for_user(session, tracker_id, user) is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            if not data.expense_ids:
                return {"deleted": 0}
            deleted = (
                session.query(Expense)
                .filter(Expense.tracker_id == tracker_id, Expense.id.in_(data.expense_ids))
                .delete(synchronize_session=False)
            )
        return {"deleted": deleted}
