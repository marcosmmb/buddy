from __future__ import annotations

from datetime import date
from typing import Annotated, Any

from litestar import Controller, Request, delete, get, post
from litestar.exceptions import HTTPException
from litestar.params import Body
from litestar.response import Response
from sqlalchemy.exc import IntegrityError

from app.db import db_session
from app.models import CsvImportConfig, Expense
from app.schemas import CsvImportConfigPayload, CsvImportPayload, CsvPreviewPayload
from app.services import expense_query, get_tracker_for_user, serialize_csv_config
from app.utils import (
    build_csv_export,
    build_csv_preview_rows,
    clean_cell,
    csv_export_filename,
    load_tracker_member_context,
    normalize_month,
    require_admin,
    require_user,
    validate_expense_payload,
)


class CsvController(Controller):
    path = "/api/trackers/{tracker_id:int}"

    @get("/csv-configs")
    def csv_configs(self, request: Request, tracker_id: int) -> list[dict[str, Any]]:
        user = require_user(request)
        with db_session() as session:
            if get_tracker_for_user(session, tracker_id, user) is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            configs = session.query(CsvImportConfig).filter(CsvImportConfig.tracker_id == tracker_id).order_by(CsvImportConfig.name).all()
            return [serialize_csv_config(config) for config in configs]

    @post("/csv-configs")
    def create_csv_config(self, request: Request, tracker_id: int, data: Annotated[CsvImportConfigPayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        require_admin(user)
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            field_map = {key: clean_cell(value) for key, value in data.field_map.items() if clean_cell(value)}
            config = CsvImportConfig(
                tracker_id=tracker_id,
                name=data.name.strip(),
                field_map=field_map,
                invert_amount=data.invert_amount,
                currency=tracker.default_currency,
                created_by_id=user.id,
            )
            session.add(config)
            try:
                session.flush()
            except IntegrityError as exc:
                raise HTTPException(status_code=409, detail="A CSV config with that name already exists") from exc
            return serialize_csv_config(config)

    @delete("/csv-configs/{config_id:int}", status_code=200)
    def delete_csv_config(self, request: Request, tracker_id: int, config_id: int) -> dict[str, str]:
        user = require_user(request)
        require_admin(user)
        with db_session() as session:
            deleted = session.query(CsvImportConfig).filter(CsvImportConfig.id == config_id, CsvImportConfig.tracker_id == tracker_id).delete()
            if not deleted:
                raise HTTPException(status_code=404, detail="CSV config not found")
        return {"status": "ok"}

    @post("/csv-imports/preview", status_code=200)
    def preview_csv_import(self, request: Request, tracker_id: int, data: Annotated[CsvPreviewPayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = load_tracker_member_context(session, tracker_id, user)
            config = session.get(CsvImportConfig, data.config_id)
            if config is None or config.tracker_id != tracker_id:
                raise HTTPException(status_code=404, detail="CSV config not found")
            return build_csv_preview_rows(session, tracker, tracker_id, config, data)

    @post("/csv-imports")
    def import_csv(self, request: Request, tracker_id: int, data: Annotated[CsvImportPayload, Body()]) -> dict[str, Any]:
        user = require_user(request)
        with db_session() as session:
            tracker = load_tracker_member_context(session, tracker_id, user)
            imported = 0
            skipped: list[dict[str, Any]] = []
            for index, row in enumerate(data.expenses, start=1):
                try:
                    validate_expense_payload(session, tracker, tracker_id, row)
                    session.add(
                        Expense(
                            tracker_id=tracker_id,
                            category_id=row.category_id,
                            paid_by_id=row.paid_by_id,
                            date=row.date,
                            amount=row.amount,
                            currency=tracker.default_currency,
                            description=row.description.strip(),
                            is_shared=row.is_shared,
                        )
                    )
                    imported += 1
                except Exception as exc:
                    skipped.append({"row": index, "reason": str(exc)})
            session.flush()
            return {"imported": imported, "skipped": skipped}

    @get("/csv-exports")
    def export_csv(self, request: Request, tracker_id: int, config_id: int, month: str | None = None) -> Response[str]:
        user = require_user(request)
        selected_month = normalize_month(month or date.today().strftime("%Y-%m"))
        with db_session() as session:
            tracker = get_tracker_for_user(session, tracker_id, user)
            if tracker is None:
                raise HTTPException(status_code=404, detail="Tracker not found")
            config = session.get(CsvImportConfig, config_id)
            if config is None or config.tracker_id != tracker_id:
                raise HTTPException(status_code=404, detail="CSV config not found")
            rows = expense_query(session, tracker_id, month=selected_month).all()
            filename = csv_export_filename(tracker, config, selected_month)
            return Response(
                content=build_csv_export(config, rows),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
