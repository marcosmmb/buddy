from __future__ import annotations

import os
import tomllib
from html import escape

from litestar import Controller, get
from litestar.response import Response

from app.utils import FRONTEND_DIR


def project_version() -> str:
    version = os.getenv("APP_VERSION", "").strip()
    if version and version != "dev":
        return version
    metadata = FRONTEND_DIR.parent / "pyproject.toml"
    if metadata.is_file():
        with metadata.open("rb") as file:
            return tomllib.load(file)["project"]["version"]
    return "dev"


class FrontendController(Controller):
    @get("/", sync_to_thread=True)
    def index(self) -> Response[str]:
        content = (FRONTEND_DIR / "index.html").read_text().replace(
            '<meta name="app-version" content="" />',
            f'<meta name="app-version" content="{escape(project_version(), quote=True)}" />',
        )
        return Response(content=content, media_type="text/html", headers={"Cache-Control": "no-cache"})
