from __future__ import annotations

from litestar import Litestar
from litestar.static_files.config import StaticFilesConfig

from app.db import init_database
from app.routes import route_handlers
from app.utils import FRONTEND_DIR, normalize_legacy_category_colors


app = Litestar(
    route_handlers=route_handlers,
    on_startup=[init_database, normalize_legacy_category_colors],
    static_files_config=[
        StaticFilesConfig(path="/static", directories=[FRONTEND_DIR / "static"]),
    ],
)
