from __future__ import annotations

import os
import tomllib
import unittest
from unittest.mock import patch

from litestar import Litestar
from litestar.testing import TestClient

from app.routes.frontend import FrontendController
from app.utils import FRONTEND_DIR


class FrontendVersionTests(unittest.TestCase):
    def get_page(self) -> str:
        with TestClient(Litestar(route_handlers=[FrontendController])) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            return response.text

    def test_local_and_default_docker_build_use_project_version(self) -> None:
        with (FRONTEND_DIR.parent / "pyproject.toml").open("rb") as file:
            version = tomllib.load(file)["project"]["version"]
        for configured_version in ("", "dev"):
            with self.subTest(configured_version=configured_version), patch.dict(os.environ, APP_VERSION=configured_version):
                self.assertIn(f'<meta name="app-version" content="{version}" />', self.get_page())

    def test_release_version_takes_precedence(self) -> None:
        with patch.dict(os.environ, APP_VERSION="1.2.3"):
            self.assertIn('<meta name="app-version" content="1.2.3" />', self.get_page())

    def test_version_is_escaped_as_html_attribute(self) -> None:
        with patch.dict(os.environ, APP_VERSION='1.2.3"><script>alert(1)</script>'):
            page = self.get_page()
            self.assertNotIn('<script>alert(1)</script>', page)
            self.assertIn('content="1.2.3&quot;&gt;&lt;script&gt;', page)
