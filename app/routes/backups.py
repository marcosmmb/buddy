from __future__ import annotations

import json
from typing import Annotated, Any

from litestar import Controller, Request, get, post
from litestar.params import Body
from litestar.response import Response

from app.backups import export_tracker_backup, restore_tracker_backup
from app.db import db_session
from app.utils import require_admin, require_user


class BackupController(Controller):
    path = "/api/trackers/{tracker_id:int}/backup"

    @get()
    def export_backup(self, request: Request, tracker_id: int) -> Response[str]:
        user = require_user(request)
        require_admin(user)
        with db_session() as session:
            data = export_tracker_backup(session, tracker_id)
            filename = backup_filename(data["tracker"]["name"])
            return Response(
                content=json.dumps(data, indent=2, sort_keys=True),
                media_type="application/json",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )

    @post("/restore")
    def restore_backup(self, request: Request, tracker_id: int, data: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        user = require_user(request)
        require_admin(user)
        with db_session() as session:
            return restore_tracker_backup(session, tracker_id, data, user)


def backup_filename(tracker_name: str) -> str:
    raw = f"{tracker_name}-backup.json".lower()
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in raw)
