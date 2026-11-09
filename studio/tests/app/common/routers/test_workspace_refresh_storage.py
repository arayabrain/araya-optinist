"""POST /workspaces/refresh-storage with S3's answers stubbed.

- While one of its listings is held, /health and a database read are answered.
  The held listing waits until /health is answered, 2 s at most: run on the
  event loop it holds /health too, so the wait times out; run in a thread it is
  released by the answer.
- A workspace that fails leaves the others refreshed; a database that cannot
  be reached stops the refresh.
- A workspace shared with others is refreshed and counted once.
- In a process, a workspace is refreshed, and a user's total scanned, by one
  request at a time; a refresh frees its scan slot between workspaces.

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
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlmodel import Session, select

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.cloud.storage_tracking import get_current_user_storage_usage
from studio.app.common.core.storage import storage_scan
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.storage.storage_scan import run_storage_scan
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
    """Answers every S3 call, recording each listing's prefix in order; holds
    the first listing `matches` picks."""

    def __init__(self, matches):
        self.matches = matches
        self.started, self.answered = threading.Event(), threading.Event()
        # True when the answer, not the timeout, released the listing
        self.released_by_answer = []
        self.listings = []

    def __call__(self, operation_name, params):
        assert operation_name == "ListObjectsV2"
        self.listings.append(params["Prefix"])
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
    # The client made with these credentials is dropped after the test
    monkeypatch.setattr(storage_scan, "_s3_client", None)


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


def seed(engine, workspace_ids, shared_with=()):
    """The user's workspaces, each shared with the users in shared_with."""
    with Session(engine) as session:
        session.add(Organization(id=1, name="Organization"))
        for user_id in (STUB_USER_ID, *shared_with):
            session.add(
                User(
                    id=user_id,
                    organization_id=1,
                    uid=f"uid-{user_id}",
                    name="User",
                    email=f"user{user_id}@example.com",
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
            for user_id in shared_with:
                session.add(
                    WorkspacesShareUser(workspace_id=workspace_id, user_id=user_id)
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error, status_code",
    [
        (PoolTimeoutError("QueuePool limit reached"), 429),
        (OperationalError("SELECT 1", {}, Exception("connection lost")), 500),
    ],
    ids=["pool_wait_timeout", "lost_connection"],
)
async def test_a_database_that_cannot_be_reached_stops_the_refresh(
    tmp_path, s3_environment, error, status_code
):
    attempted = []

    def unreachable(db, bucket_name, workspace_id, **kwargs):
        attempted.append(workspace_id)
        raise error

    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, TABLES
    ), stub_token_and_user_row(), patch.object(
        BaseClient, "_make_api_call", lambda client, name, params: s3_listing(params)
    ), patch.object(
        WorkspaceDataCapacityService, "is_available", return_value=True
    ), patch.object(
        CloudWorkspaceDataCapacityService,
        "update_workspace_data_usage_with_s3",
        unreachable,
    ):
        seed(engine, FAILURE_CASE_IDS)
        async with asgi_client(refresh_app()) as client:
            response = await client.post(
                "/workspaces/refresh-storage", headers=AUTH_HEADERS
            )

    assert len(attempted) == 1, "the refresh went on to the next workspaces"
    assert response.status_code == status_code
    if status_code == 429:
        assert response.headers["Retry-After"] == "5"


@pytest.mark.asyncio
async def test_a_shared_workspace_is_refreshed_and_counted_once(
    tmp_path, s3_environment
):
    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, TABLES
    ), stub_token_and_user_row(), patch.object(
        BaseClient, "_make_api_call", lambda client, name, params: s3_listing(params)
    ), patch.object(
        WorkspaceDataCapacityService, "is_available", return_value=True
    ):
        # The owner's workspace, shared with two other users
        seed(engine, [WORKSPACE_ID], shared_with=(2, 3))
        async with asgi_client(refresh_app()) as client:
            response = await client.post(
                "/workspaces/refresh-storage", headers=AUTH_HEADERS
            )
        stored = stored_usage(engine, [WORKSPACE_ID])

    assert response.status_code == 200
    body = response.json()
    assert (body["refreshed_workspaces"], body["total_workspaces"]) == (1, 1)
    assert stored == ({WORKSPACE_ID: REFRESHED}, INPUT_BYTES + EXPERIMENT_BYTES)


@pytest.mark.asyncio
async def test_two_refreshes_of_a_workspace_run_one_at_a_time(tmp_path, s3_environment):
    # The first refresh's input listing of the workspace
    hold = HeldListing(lambda params: "MaxKeys" not in params)

    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, TABLES
    ), stub_token_and_user_row(), patch.object(
        BaseClient, "_make_api_call", hold
    ), patch.object(
        WorkspaceDataCapacityService, "is_available", return_value=True
    ):
        seed(engine, [WORKSPACE_ID])
        async with asgi_client(refresh_app()) as client:
            first = asyncio.create_task(
                client.post("/workspaces/refresh-storage", headers=AUTH_HEADERS)
            )
            await wait_until(hold.started.is_set, timeout=5)
            second = asyncio.create_task(
                client.post("/workspaces/refresh-storage", headers=AUTH_HEADERS)
            )
            # Time for the second to reach the workspace, were it not to wait
            await asyncio.sleep(0.5)
            listed_while_held = list(hold.listings)
            hold.answered.set()
            responses = await asyncio.gather(first, second)
        stored = stored_usage(engine, [WORKSPACE_ID])

    assert len(listed_while_held) == 1, "the second refresh listed the workspace"
    assert [response.status_code for response in responses] == [200, 200]
    # One experiment record: the second refresh found the first one's
    assert stored == ({WORKSPACE_ID: REFRESHED}, INPUT_BYTES + EXPERIMENT_BYTES)


@pytest.mark.asyncio
async def test_two_live_totals_of_a_user_are_scanned_one_at_a_time(
    tmp_path, s3_environment
):
    # The first total's streaming listing
    hold = HeldListing(lambda params: "MaxKeys" in params)

    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, TABLES
    ), patch.object(BaseClient, "_make_api_call", hold):
        seed(engine, [WORKSPACE_ID])
        first = asyncio.create_task(
            get_current_user_storage_usage(STUB_USER_ID, force_live=True)
        )
        await wait_until(hold.started.is_set, timeout=5)
        second = asyncio.create_task(
            get_current_user_storage_usage(STUB_USER_ID, force_live=True)
        )
        # Time for the second to start listing, were it not to wait
        await asyncio.sleep(0.5)
        listed_while_held = list(hold.listings)
        hold.answered.set()
        totals = await asyncio.gather(first, second)

    assert len(listed_while_held) == 1, "the second total listed while the first ran"
    assert totals == [INPUT_BYTES + EXPERIMENT_BYTES] * 2


@pytest.mark.asyncio
async def test_a_refresh_frees_its_scan_slot_between_workspaces(
    tmp_path, s3_environment, monkeypatch
):
    # One slot, so who takes it next shows in the order of the listings
    monkeypatch.setattr(storage_scan, "MAX_CONCURRENT_SCANS", 1)
    workspace_ids = FAILURE_CASE_IDS[:2]
    # The input listing of whichever workspace the refresh takes first
    hold = HeldListing(lambda params: "MaxKeys" not in params)

    def other_scan():
        hold.listings.append("other scan")

    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, TABLES
    ), stub_token_and_user_row(), patch.object(
        BaseClient, "_make_api_call", hold
    ), patch.object(
        WorkspaceDataCapacityService, "is_available", return_value=True
    ):
        seed(engine, workspace_ids)
        async with asgi_client(refresh_app()) as client:
            refresh = asyncio.create_task(
                client.post("/workspaces/refresh-storage", headers=AUTH_HEADERS)
            )
            await wait_until(hold.started.is_set, timeout=5)
            # Queued for the slot the refresh's first workspace holds
            other = asyncio.create_task(run_storage_scan(other_scan))
            await asyncio.sleep(0.2)
            hold.answered.set()
            response = await refresh
            await other

    assert response.status_code == 200
    first_input = hold.listings[0]
    second_input = next(
        S3StorageController.make_s3_input_prefix(str(workspace_id))
        for workspace_id in workspace_ids
        if S3StorageController.make_s3_input_prefix(str(workspace_id)) != first_input
    )
    assert hold.listings.index("other scan") < hold.listings.index(
        second_input
    ), "the refresh kept its slot for the next workspace"
