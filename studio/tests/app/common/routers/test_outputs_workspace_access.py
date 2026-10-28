"""The visualization routes read a workspace's outputs and inputs by path, so a
signed-in caller must own the workspace or have it shared with them.

conftest.py stubs the outputs auth for the whole session, so these tests hand
it a real user and give the workspace check a database to answer from.
"""
import os
import shutil

import pytest
from fastapi.testclient import TestClient

from studio.__main_unit__ import app
from studio.app.common.core.auth.auth_dependencies import (
    get_current_user_with_dataview_outputs_check,
)
from studio.app.common.db.database import get_db
from studio.app.common.models import Workspace, WorkspacesShareUser
from studio.app.common.schemas.users import Organization, User
from studio.app.dir_path import DIRPATH
from studio.tests.app.common.sqlite_harness import sqlite_session

OWNER, SHARED, STRANGER = 1, 2, 3
HTML_PATH = "1/wsaccess/glm/summary.html"

URLS = [
    f"/api/visualizations/html/{HTML_PATH}",
    f"/api/visualizations/html/{DIRPATH.OUTPUT_DIR}/{HTML_PATH}",
    "/api/visualizations/data/1/wsaccess/glm/x.json",
    "/api/visualizations/inittimedata/1/wsaccess/fn/fluorescence.json",
    "/api/visualizations/timedata/1/wsaccess/fn/fluorescence.json?index=0",
    "/api/visualizations/alltimedata/1/wsaccess/fn/fluorescence.json",
    "/api/visualizations/image/stack.tif?workspace_id=1",
    "/api/visualizations/csv/x.csv?workspace_id=1",
    "/api/visualizations/thumbnail/1/wsaccess/input",
    "/api/visualizations/structured/1/wsaccess/node",
    "/api/visualizations/image/1/wsaccess/fn/cell_roi.json?workspace_id=1",
]


@pytest.fixture()
def db():
    with sqlite_session(
        [Workspace.__table__, WorkspacesShareUser.__table__]
    ) as session:
        session.add(Workspace(id=1, name="ws", user_id=OWNER, deleted=False))
        session.add(Workspace(id=2, name="own", user_id=STRANGER, deleted=False))
        session.add(WorkspacesShareUser(id=1, workspace_id=1, user_id=SHARED))
        session.commit()
        yield session


@pytest.fixture()
def get_as(db, monkeypatch):
    async def no_sync(*_):
        pass

    monkeypatch.setattr(
        "studio.app.common.routers.outputs._ensure_visualization_synced", no_sync
    )
    html_file = os.path.join(DIRPATH.OUTPUT_DIR, HTML_PATH)
    os.makedirs(os.path.dirname(html_file), exist_ok=True)
    with open(html_file, "w") as f:
        f.write("<table>summary</table>")

    original = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: db

    def get(url, user_id):
        app.dependency_overrides[
            get_current_user_with_dataview_outputs_check
        ] = lambda: User(
            id=user_id,
            uid=f"uid-{user_id}",
            email=f"user{user_id}@example.com",
            organization=Organization(id=1, name="org"),
        )
        # Members reach the routes, which may fail on the missing fixtures
        return TestClient(app, raise_server_exceptions=False).get(url)

    yield get
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)
    shutil.rmtree(os.path.join(DIRPATH.OUTPUT_DIR, "1", "wsaccess"))


@pytest.mark.parametrize("user_id", [OWNER, SHARED])
def test_member_reads_html_output(get_as, user_id):
    response = get_as(f"/api/visualizations/html/{HTML_PATH}", user_id)
    assert response.status_code == 200
    assert response.json()["data"] == "<table>summary</table>"


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize("user_id", [OWNER, SHARED])
def test_member_passes_the_workspace_check(get_as, url, user_id):
    assert get_as(url, user_id).status_code != 403


@pytest.mark.parametrize("url", URLS)
def test_non_member_is_refused(get_as, url):
    assert get_as(url, STRANGER).status_code == 403


def test_output_path_outside_the_named_workspace_is_refused(get_as):
    url = "/api/visualizations/image/1/wsaccess/fn/cell_roi.json?workspace_id=2"
    assert get_as(url, STRANGER).status_code == 403
