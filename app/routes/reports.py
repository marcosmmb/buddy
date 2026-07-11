from __future__ import annotations

from datetime import date
from typing import Any

from litestar import Controller, Request, get
from litestar.exceptions import HTTPException

from app.db import db_session
from app.services import (
    balance_for_tracker,
    expense_query,
    get_tracker_for_user,
    member_breakdown_for_expenses,
    monthly_share_overrides,
    monthly_totals_for_year,
    overview_for_expenses,
    period_options,
    serialize_expense,
)
from app.utils import require_user


class ReportController(Controller):
    path = "/api/trackers/{tracker_id:int}"

    @get("/period-options")
    def tracker_period_options(self, request: Request, tracker_id: int) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            if get_tracker_for_user(session, tracker_id, user) is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            return period_options(session, tracker_id)

    @get("/overview")
    def overview(
        self,
        request: Request,
        tracker_id: int,
        period_type: str = "month",
        period: str | None = None,
        month: str | None = None,
        year: int | None = None,
    ) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            if month:
                period_type = "month"
                period = month
            elif year:
                period_type = "year"
                period = str(year)
            elif period is None:
                period = date.today().strftime("%Y-%m")
            if period_type == "year":
                selected_year = int(period or date.today().year)
                rows = expense_query(session, tracker_id, year=selected_year).all()
                return {
                    "period_type": "year",
                    "period": str(selected_year),
                    "summary": overview_for_expenses(rows),
                    "member_breakdown": member_breakdown_for_expenses(session, tracker, rows),
                    "monthly_totals": monthly_totals_for_year(session, tracker_id, selected_year),
                    "expenses": [serialize_expense(expense) for expense in rows],
                }
            rows = expense_query(session, tracker_id, month=period).all()
            share_overrides = monthly_share_overrides(session, tracker_id, period)
            return {
                "period_type": "month",
                "period": period,
                "summary": overview_for_expenses(rows),
                "member_breakdown": member_breakdown_for_expenses(session, tracker, rows),
                "balance": balance_for_tracker(tracker, rows, share_overrides),
                "monthly_totals": [],
                "expenses": [serialize_expense(expense) for expense in rows],
            }
