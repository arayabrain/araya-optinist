"""The HDF5 and MAT structure routes read a workspace's input tree, so they must
check that the caller owns the workspace or has it shared with them.

conftest.py replaces is_workspace_available for the whole session, so these
tests put the real check back and give it a database to answer from.
"""
import pytest

from studio.__main_unit__ import app
from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.workspace.workspace_dependencies import (
    is_workspace_available,
)
from studio.app.common.db.database import get_db
from studio.app.common.models import Workspace, WorkspacesShareUser
from studio.app.common.schemas.users import Organization, User
from studio.tests.app.common.sqlite_harness import sqlite_session

OWNER, SHARED, STRANGER = 1, 2, 3

URLS = [
    "/hdf5/files/test.nwb?workspace_id=1",
    "/mat/files/test.mat?workspace_id=1",
]


@pytest.fixture()
def db():
    with sqlite_session(
        [Workspace.__table__, WorkspacesShareUser.__table__]
    ) as session:
        session.add(Workspace(id=1, name="ws", user_id=OWNER, deleted=False))
        session.add(WorkspacesShareUser(id=1, workspace_id=1, user_id=SHARED))
        session.commit()
        yield session


@pytest.fixture()
def get_as(client, db, monkeypatch):
    # No .mat fixture exists; a cached tree is also what the route would leak.
    monkeypatch.setattr(
        "studio.app.optinist.routers.mat.get_mat_structure_dict",
        lambda _: {"files/test.mat": []},
    )
    original = app.dependency_overrides.copy()
    del app.dependency_overrides[is_workspace_available]
    app.dependency_overrides[get_db] = lambda: db

    def get(url, user_id):
        app.dependency_overrides[get_current_user] = lambda: User(
            id=user_id,
            uid=f"uid-{user_id}",
            email=f"user{user_id}@example.com",
            organization=Organization(id=1, name="org"),
        )
        return client.get(url)

    yield get
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


@pytest.mark.parametrize("url", URLS)
@pytest.mark.parametrize(
    "user_id,status", [(OWNER, 200), (SHARED, 200), (STRANGER, 403)]
)
def test_structure_route_checks_workspace_membership(get_as, url, user_id, status):
    assert get_as(url, user_id).status_code == status
