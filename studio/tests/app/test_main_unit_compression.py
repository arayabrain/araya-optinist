"""The app gzips its responses, and serves the bundle's gzip copies."""

from unittest.mock import patch

from studio.__main_unit__ import app
from studio.app.common.core.middleware.compression_middleware import (
    PrecompressedStaticFiles,
)
from studio.app.dir_path import DIRPATH

GZIP = {"accept-encoding": "gzip, deflate"}


def test_an_api_response_is_gzipped(client):
    """/openapi is a whole JSON body served on every tier, without auth."""
    response = client.get("/openapi", headers=GZIP)

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.json()["paths"]


def test_the_shell_from_the_spa_middleware_is_gzipped(client, tmp_path):
    """SPARoutingMiddleware answers a browser's /workspaces itself, so this
    holds only with the compression added outside it."""
    (tmp_path / "index.html").write_text(
        f'<html><body><div id="root"></div>{"<p>shell</p>" * 200}</body></html>',
        encoding="utf-8",
    )

    with patch.object(DIRPATH.FRONTEND_DIRS, "BUILD", str(tmp_path)):
        response = client.get("/workspaces", headers={**GZIP, "accept": "text/html"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert '<div id="root">' in response.text


def test_static_files_are_served_with_their_gzip_copies():
    (static,) = [r for r in app.routes if getattr(r, "path", "") == "/static"]

    assert isinstance(static.app, PrecompressedStaticFiles)
