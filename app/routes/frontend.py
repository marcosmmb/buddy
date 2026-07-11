from __future__ import annotations

from litestar import Controller, get
from litestar.response import Response

from app.utils import FRONTEND_DIR


class FrontendController(Controller):
    @get("/")
    def index(self) -> Response[str]:
        return Response(content=(FRONTEND_DIR / "index.html").read_text(), media_type="text/html")
