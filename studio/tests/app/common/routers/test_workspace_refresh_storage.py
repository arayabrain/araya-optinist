"""POST /workspaces/refresh-storage with S3's answers stubbed.

- While one of its listings is held, /health and a database read are answered.
  The held listing waits until /health is answered, 2 s at most: run on the
  event loop it holds /health too, so the wait times out; run in a thread it is
  released by the answer.
- A workspace that fails leaves the others refreshed.

The real auth dependency, route and storage helpers run on a real
one-connection pool (db_pool_probe); the token check, the user row and S3's
answers (each client's API call) are stubbed.
"""

import asyncio
import threading
from unittest.mock import patch

import pytest
from botocore.client import BaseClient
from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlmodel import Session, select

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.workspace.workspace_data_capacity_services import (
    WorkspaceDataCapacityService,
)
from studio.app.common.db.database import session_scope
from studio.app.common.models import (
    ExperimentRecord,
    Organization,
    Role,
    SubscriptionPlans,
    User,
    UserRole,
    UserStorageUsage,
    UserSubscription,
    Workspace,
    WorkspacesShareUser,
)
from studio.app.common.routers import workspace
from studio.app.dir_path import DIRPATH
from studio.scripts.run_sync_data_capacity_cloud import (
    CloudWorkspaceDataCapacityService,
)
from studio.tests.app.common.db_pool_probe import (
    AUTH_HEADERS,
    STUB_USER_ID,
    asgi_client,
    one_connection_pool,
    stub_token_and_user_row,
    wait_until,
)
from studio.tests.app.common.sqlite_harness import sqlite_tables

WORKSPACE_ID = 987654
# Refreshed in this order; the middle one fails
FAILURE_CASE_IDS = (987651, 987652, 987653)
FAILING_ID = 987652
EXPERIMENT_ID = "exp1"
# Each workspace's input (123 B) and one experiment (400 B)
INPUT_BYTES, EXPERIMENT_BYTES = 123, 400
TABLES = [
    table.__table__
    for table in (
        Organization,
        User,
        Role,
        UserRole,
        SubscriptionPlans,
        UserSubscription,
        UserStorageUsage,
        Workspace,
        WorkspacesShareUser,
        ExperimentRecord,
    )
]


def workspace_objects(workspace_id):
    input_prefix = S3StorageController.make_s3_input_prefix(str(workspace_id))
    experiment_prefix = S3StorageController.make_s3_output_prefix(
        str(workspace_id), EXPERIMENT_ID
    )
    return {
        f"{input_prefix}movie.tif": 100,
        f"{input_prefix}behavior.csv": 23,
        f"{experiment_prefix}experiment.yaml": EXPERIMENT_BYTES,
    }


S3_OBJECTS = {
    key: size
    for workspace_id in (WORKSPACE_ID, *FAILURE_CASE_IDS)
    for key, size in workspace_objects(workspace_id).items()
}


def s3_listing(params):
    """ListObjectsV2's answer over S3_OBJECTS, one page."""
    prefix, delimiter = params["Prefix"], params.get("Delimiter")
    contents, common_prefixes = [], set()
    for key, size in S3_OBJECTS.items():
        if not key.startswith(prefix):
            continue
        rest = key[len(prefix) :]
        if delimiter and delimiter in rest:
            common_prefixes.add(prefix + rest.split(delimiter)[0] + delimiter)
        else:
            contents.append({"Key": key, "Size": size})
    page = {"IsTruncated": False, "KeyCount": len(contents)}
    if contents:
        page["Contents"] = contents
    if common_prefixes:
        page["CommonPrefixes"] = [{"Prefix": p} for p in sorted(common_prefixes)]
    return page


class HeldListing:
    """Answers every S3 call; holds the first listing `matches` picks."""

    def __init__(self, matches):
        self.matches = matches
        self.started, self.answered = threading.Event(), threading.Event()
        # True when the answer, not the timeout, released the listing
        self.released_by_answer = []

    def __call__(self, operation_name, params):
        assert operation_name == "ListObjectsV2"
        if self.matches(params) and not self.started.is_set():
            self.started.set()
            self.released_by_answer.append(self.answered.wait(2))
        return s3_listing(params)


@pytest.fixture
def s3_environment(tmp_path, monkeypatch):
    """S3 mode with offline credentials, and empty local data folders."""
    monkeypatch.setenv("REMOTE_STORAGE_TYPE", "2")
    monkeypatch.setenv("S3_DEFAULT_BUCKET_NAME", "test-bucket")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setattr(DIRPATH, "INPUT_DIR", str(tmp_path / "input"))
    monkeypatch.setattr(DIRPATH, "OUTPUT_DIR", str(tmp_path / "output"))


def refresh_app():
    """The workspace router behind the real auth, and two probes without auth."""
    app = FastAPI()
    app.include_router(workspace.router, dependencies=[Depends(get_current_user)])

    @app.get("/health")
    async def health_endpoint():
        return {}

    # A sync route: it waits for the pool's one connection in a thread
    @app.get("/database")
    def database_endpoint():
        with session_scope() as db:
            db.execute(text("SELECT 1"))
        return {}

    return app


def seed(engine, workspace_ids):
    with Session(engine) as session:
        session.add(Organization(id=1, name="Organization"))
        session.add(
            User(
                id=STUB_USER_ID,
                organization_id=1,
                uid="uid-1",
                name="User",
                email="user@example.com",
                attributes={},
                active=True,
            )
        )
        for workspace_id in workspace_ids:
            session.add(
                Workspace(
                    id=workspace_id, name="ws", user_id=STUB_USER_ID, deleted=False
                )
            )
        session.commit()


def stored_usage(engine, workspace_ids):
    """Per workspace: (input usage, [(experiment, usage)]); and the user total."""
    with Session(engine) as session:
        workspaces = {
            workspace_id: (
                session.get(Workspace, workspace_id).input_data_usage,
                [
                    (record.uid, record.data_usage)
                    for record in session.exec(
                        select(ExperimentRecord).where(
                            ExperimentRecord.workspace_id == workspace_id
                        )
                    )
                ],
            )
            for workspace_id in workspace_ids
        }
        user = session.exec(
            select(UserStorageUsage.storage_usage_bytes).where(
                UserStorageUsage.user_id == STUB_USER_ID
            )
        ).one()
    return workspaces, user


REFRESHED = (INPUT_BYTES, [(EXPERIMENT_ID, EXPERIMENT_BYTES)])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "held",
    [
        # The workspace refresh pages through its input prefix
        pytest.param(lambda params: "MaxKeys" not in params, id="workspace_listing"),
        # The user total streams each prefix a page of MaxKeys at a time
        pytest.param(lambda params: "MaxKeys" in params, id="user_total_listing"),
    ],
)
async def test_the_api_answers_while_a_refresh_lists_s3(tmp_path, s3_environment, held):
    hold = HeldListing(held)

    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, TABLES
    ), stub_token_and_user_row(), patch.object(
        BaseClient, "_make_api_call", hold
    ), patch.object(
        WorkspaceDataCapacityService, "is_available", return_value=True
    ):
        seed(engine, [WORKSPACE_ID])
        async with asgi_client(refresh_app()) as client:
            refresh = asyncio.create_task(
                client.post("/workspaces/refresh-storage", headers=AUTH_HEADERS)
            )
            await wait_until(hold.started.is_set, timeout=5)
            health = await asyncio.wait_for(client.get("/health"), timeout=5)
            try:
                database = await asyncio.wait_for(client.get("/database"), timeout=1)
            except asyncio.TimeoutError:
                database = None
            hold.answered.set()
            response = await refresh
        stored = stored_usage(engine, [WORKSPACE_ID])

    assert hold.released_by_answer == [True], "the listing held the event loop"
    assert health.status_code == 200
    assert database is not None, "the refresh held the pooled connection while listing"
    assert database.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert (body["refreshed_workspaces"], body["total_workspaces"]) == (1, 1)
    assert stored == ({WORKSPACE_ID: REFRESHED}, INPUT_BYTES + EXPERIMENT_BYTES)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failing_phase, failed_workspace",
    [
        # Fails before its own commit: nothing of it is stored
        ("input", (0, [])),
        # Fails after staging its experiment records: its input phase had
        # committed, the records are rolled back
        ("experiments", (INPUT_BYTES, [])),
    ],
)
async def test_a_failed_workspace_leaves_the_others_refreshed(
    tmp_path, s3_environment, failing_phase, failed_workspace
):
    phase = {
        "input": "update_workspace_data_usage_with_s3",
        "experiments": "recalculate_workspace_data_capacity_with_s3",
    }[failing_phase]
    real_phase = getattr(CloudWorkspaceDataCapacityService, phase)

    def fail_one_workspace(db, bucket_name, workspace_id, **kwargs):
        failing = workspace_id == str(FAILING_ID)
        if failing and failing_phase == "input":
            raise RuntimeError("the input phase failed")
        real_phase(db, bucket_name, workspace_id, **kwargs)
        if failing:
            raise RuntimeError("the experiment phase failed")

    def answer(client, operation_name, params):
        return s3_listing(params)

    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, TABLES
    ), stub_token_and_user_row(), patch.object(
        BaseClient, "_make_api_call", answer
    ), patch.object(
        WorkspaceDataCapacityService, "is_available", return_value=True
    ), patch.object(
        CloudWorkspaceDataCapacityService, phase, fail_one_workspace
    ):
        seed(engine, FAILURE_CASE_IDS)
        async with asgi_client(refresh_app()) as client:
            response = await client.post(
                "/workspaces/refresh-storage", headers=AUTH_HEADERS
            )
        stored = stored_usage(engine, FAILURE_CASE_IDS)

    assert response.status_code == 200
    body = response.json()
    assert (body["refreshed_workspaces"], body["total_workspaces"]) == (2, 3)
    expected = {workspace_id: REFRESHED for workspace_id in FAILURE_CASE_IDS}
    expected[FAILING_ID] = failed_workspace
    assert stored == (expected, 3 * (INPUT_BYTES + EXPERIMENT_BYTES))
