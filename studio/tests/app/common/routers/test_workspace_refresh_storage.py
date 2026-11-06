"""POST /workspaces/refresh-storage while one of its S3 listings is held.

The held listing waits until /health is answered, 2 s at most: run on the
event loop it holds /health too, so the wait times out; run in a thread it is
released by the answer.

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
from sqlmodel import Session, select

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.workspace.workspace_data_capacity_services import (
    WorkspaceDataCapacityService,
)
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
EXPERIMENT_ID = "exp1"
INPUT_PREFIX = S3StorageController.make_s3_input_prefix(str(WORKSPACE_ID))
EXPERIMENT_PREFIX = S3StorageController.make_s3_output_prefix(
    str(WORKSPACE_ID), EXPERIMENT_ID
)
S3_OBJECTS = {
    f"{INPUT_PREFIX}movie.tif": 100,
    f"{INPUT_PREFIX}behavior.csv": 23,
    f"{EXPERIMENT_PREFIX}experiment.yaml": 400,
}
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


def seed(engine):
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
        session.add(
            Workspace(id=WORKSPACE_ID, name="ws", user_id=STUB_USER_ID, deleted=False)
        )
        session.commit()


def stored_usage(engine):
    with Session(engine) as session:
        return {
            "input": session.get(Workspace, WORKSPACE_ID).input_data_usage,
            "experiments": [
                (record.uid, record.data_usage)
                for record in session.exec(select(ExperimentRecord))
            ],
            "user": session.exec(
                select(UserStorageUsage.storage_usage_bytes).where(
                    UserStorageUsage.user_id == STUB_USER_ID
                )
            ).one(),
        }


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
    app = FastAPI()
    app.include_router(workspace.router, dependencies=[Depends(get_current_user)])

    @app.get("/health")
    async def health_endpoint():
        return {}

    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, TABLES
    ), stub_token_and_user_row(), patch.object(
        BaseClient, "_make_api_call", hold
    ), patch.object(
        WorkspaceDataCapacityService, "is_available", return_value=True
    ):
        seed(engine)
        async with asgi_client(app) as client:
            refresh = asyncio.create_task(
                client.post("/workspaces/refresh-storage", headers=AUTH_HEADERS)
            )
            await wait_until(hold.started.is_set, timeout=5)
            health = await asyncio.wait_for(client.get("/health"), timeout=5)
            hold.answered.set()
            response = await refresh
        stored = stored_usage(engine)

    assert hold.released_by_answer == [True], "the listing held the event loop"
    assert health.status_code == 200
    assert response.status_code == 200
    body = response.json()
    assert (body["refreshed_workspaces"], body["total_workspaces"]) == (1, 1)
    assert stored == {
        "input": 123,
        "experiments": [(EXPERIMENT_ID, 400)],
        "user": 523,
    }
