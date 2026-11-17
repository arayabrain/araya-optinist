"""The storage usage lookups on a one-connection pool: the storage routes the
top page and the run button send, and the quota check before a run or upload.

They read the user's storage row and subscription through session_scope. Those
lookups wait for a pooled connection in a worker thread, so another request is
answered meanwhile (db_pool_probe), and none of their statements runs on the
event loop. The same holds when a stale usage is recalculated and stored, when
its owner is no longer active, and for the refresh. When a lookup a scan needs
fails, the stored usage is kept rather than overwritten with 0. The user and S3
are stubbed; the queries run on the real pool.
"""

import asyncio
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import delete, event, update
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlmodel import Session, select

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.background.storage_reconciliation_job import (
    StorageReconciliationJob,
)
from studio.app.common.core.cloud.storage_tracking import get_user_storage_usage
from studio.app.common.core.storage.remote_storage_controller import RemoteStorageType
from studio.app.common.core.subscription.constants import (
    PlanName,
    StorageQuota,
    StorageSize,
)
from studio.app.common.core.utils.datetime_utils import get_current_datetime
from studio.app.common.db import database
from studio.app.common.models import (
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
from studio.app.common.routers import files, run, storage_limit_alerts, workspace
from studio.tests.app.common.db_pool_probe import (
    add_hold_and_health_routes,
    answered_while_waiting,
    asgi_client,
    assert_answered_while_waiting,
    one_connection_pool,
    statements_on_the_loop,
)
from studio.tests.app.common.sqlite_harness import sqlite_tables

USER_ID = 1
# No input or output directory exists for it, so a local scan measures 0
WORKSPACE_ID = 987001
USAGE_BYTES = 1024
# One stubbed S3 object per prefix; a workspace has an input and an output one
OBJECT_BYTES = 100
SCANNED_BYTES = 2 * OBJECT_BYTES


@pytest.fixture
def engine(tmp_path):
    tables = [
        Organization.__table__,
        Role.__table__,
        User.__table__,
        UserRole.__table__,
        SubscriptionPlans.__table__,
        UserSubscription.__table__,
        UserStorageUsage.__table__,
        Workspace.__table__,
        WorkspacesShareUser.__table__,
    ]
    with one_connection_pool(tmp_path / "pool.db", timeout=3) as engine, sqlite_tables(
        engine, tables
    ):
        with Session(engine) as session:
            session.add(Organization(id=1, name="Organization"))
            session.add(
                User(
                    id=USER_ID,
                    organization_id=1,
                    uid="uid-1",
                    name="User",
                    email="user@example.com",
                    attributes={"remote_bucket_name": "user-bucket"},
                    active=True,
                )
            )
            session.add(
                Workspace(
                    id=WORKSPACE_ID,
                    user_id=USER_ID,
                    name="ws",
                    deleted=False,
                    input_data_usage=0,
                )
            )
            # Fresh, so no route recalculates the usage from storage
            session.add(
                UserStorageUsage(
                    user_id=USER_ID,
                    storage_usage_bytes=USAGE_BYTES,
                    storage_quota_bytes=StorageQuota.FREE * StorageSize.GB,
                )
            )
            session.commit()
        yield engine


@pytest.fixture
def stale_engine(engine):
    with Session(engine) as session:
        session.execute(
            update(UserStorageUsage).values(
                last_updated=get_current_datetime() - timedelta(days=2)
            )
        )
        session.commit()
    return engine


@pytest.fixture
def s3_listing():
    """The S3 client of both size scans: each listed prefix holds one object."""
    client = MagicMock()
    page = {"Contents": [{"Size": OBJECT_BYTES}], "IsTruncated": False}
    client.list_objects_v2.return_value = page
    client.get_paginator.return_value.paginate.side_effect = lambda **_: [page]
    # The refresh's scan creates its own client; the recalculation's streaming
    # scan takes the process's shared one
    with patch(
        "studio.app.common.core.cloud.s3_storage_monitor.boto3.client",
        return_value=client,
    ), patch(
        "studio.app.common.core.cloud.s3_storage_monitor.get_s3_client",
        return_value=client,
    ):
        yield client


def _stored_usage(engine) -> int:
    with Session(engine) as session:
        return session.exec(select(UserStorageUsage.storage_usage_bytes)).one()


def _user():
    return SimpleNamespace(
        id=USER_ID,
        name="User",
        email="user@example.com",
        remote_bucket_name="user-bucket",
    )


def _app(released: asyncio.Event) -> FastAPI:
    app = FastAPI()
    app.include_router(storage_limit_alerts.router)
    app.dependency_overrides[get_current_user] = _user
    add_hold_and_health_routes(app, released)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path, expected",
    [
        ("/storage-limit-alerts/limit-warning", None),
        ("/storage-limit-alerts/usage", {"storage_usage_bytes": USAGE_BYTES}),
        ("/storage-limit-alerts/me", {"storage_usage_bytes": USAGE_BYTES}),
    ],
)
async def test_other_requests_are_answered_while_the_route_waits_for_the_pool(
    engine, path, expected
):
    released = asyncio.Event()
    async with asgi_client(_app(released)) as client:
        probe = await answered_while_waiting(
            client, engine, released, lambda: client.get(path)
        )

    assert_answered_while_waiting(probe)
    if expected is None:
        assert probe.response.json() is None
    else:
        assert expected.items() <= probe.response.json().items()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "storage_type, measured",
    [(RemoteStorageType.MOCK, 0), (RemoteStorageType.S3, SCANNED_BYTES)],
    ids=["local", "s3"],
)
async def test_a_stale_usage_is_recalculated_and_stored_off_the_event_loop(
    stale_engine, s3_listing, storage_type, measured
):
    with patch.object(
        RemoteStorageType, "get_activated_type", return_value=storage_type
    ):
        async with asgi_client(_app(asyncio.Event())) as client:
            with statements_on_the_loop(stale_engine) as on_loop:
                response = await client.get("/storage-limit-alerts/limit-warning")

    assert response.status_code == 200
    assert response.json() is None
    assert on_loop == []
    assert _stored_usage(stale_engine) == measured


@pytest.mark.asyncio
async def test_an_inactive_owner_keeps_the_stored_usage_off_the_event_loop(
    stale_engine, s3_listing
):
    with Session(stale_engine) as session:
        session.execute(update(User).values(active=False))
        session.commit()

    with patch.object(
        RemoteStorageType, "get_activated_type", return_value=RemoteStorageType.S3
    ):
        async with asgi_client(_app(asyncio.Event())) as client:
            with statements_on_the_loop(stale_engine) as on_loop:
                response = await client.get("/storage-limit-alerts/usage")

    assert response.status_code == 200
    assert response.json()["storage_usage_bytes"] == USAGE_BYTES
    assert on_loop == []
    assert _stored_usage(stale_engine) == USAGE_BYTES


@pytest.mark.asyncio
async def test_the_refresh_scans_and_stores_off_the_event_loop(engine, s3_listing):
    async with asgi_client(_app(asyncio.Event())) as client:
        with statements_on_the_loop(engine) as on_loop:
            response = await client.post("/storage-limit-alerts/refresh")

    assert response.status_code == 200
    assert response.json()["updated_usage_bytes"] == SCANNED_BYTES
    assert response.json()["database_updated"] is True
    assert on_loop == []
    assert _stored_usage(engine) == SCANNED_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize("router", [run, files], ids=["run", "upload"])
async def test_the_quota_check_waits_for_the_pool_off_the_event_loop(engine, router):
    with statements_on_the_loop(engine) as on_loop:
        with engine.connect():
            check = asyncio.create_task(router._check_storage_quota(USER_ID))
            started = time.monotonic()
            # A pool wait on the loop holds this sleep until the pool times out
            await asyncio.sleep(0.2)
            slept = time.monotonic() - started
            was_waiting = not check.done()

        # Under quota: returns without raising once the connection is back
        await asyncio.wait_for(check, timeout=2)

    assert slept < 1
    assert was_waiting
    assert on_loop == []


@pytest.mark.asyncio
@pytest.mark.parametrize("router", [run, files], ids=["run", "upload"])
async def test_a_fresh_usage_is_checked_against_the_quota_in_one_lookup(engine, router):
    lookups = []
    run_in_thread = database.run_in_limited_thread

    async def counted(limiter_var, size, func, *args):
        lookups.append(func.__name__)
        return await run_in_thread(limiter_var, size, func, *args)

    with patch.object(database, "run_in_limited_thread", counted):
        # Under quota: returns without raising
        await router._check_storage_quota(USER_ID)

    # Each lookup takes a run_db_lookup slot
    assert len(lookups) == 1, lookups


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "router, path, body",
    [
        (run, f"/run/{WORKSPACE_ID}", {"forceRunList": []}),
        (run, f"/run/{WORKSPACE_ID}/rerun-uid", {"forceRunList": []}),
        (
            files,
            f"/files/{WORKSPACE_ID}/download",
            {"url": "https://example.com/a.tif"},
        ),
    ],
    ids=["run", "rerun", "url-download"],
)
async def test_the_quota_check_starts_with_the_workspace_checks_connection_returned(
    engine, router, path, body
):
    connections_held = []

    async def refuse(user_id):
        connections_held.append(engine.pool.checkedout())
        raise HTTPException(status_code=403, detail="Storage quota exceeded")

    app = FastAPI()
    app.include_router(router.router)
    app.dependency_overrides[get_current_user] = _user
    with patch.object(
        router, "_check_storage_quota", new=AsyncMock(side_effect=refuse)
    ):
        async with asgi_client(app) as client:
            response = await client.post(path, json=body)

    assert response.status_code == 403
    # Held, its lookups would each take a second connection
    assert connections_held == [0]


def test_a_missing_storage_row_falls_back_on_one_connection_at_a_time(engine):
    with Session(engine) as session:
        session.execute(delete(UserStorageUsage))
        session.add(
            SubscriptionPlans(
                id=2,
                name=PlanName.PREMIUM,
                price=1,
                billing_cycle=1,
                features={},
                status=True,
                currency=1,
            )
        )
        session.add(
            UserSubscription(
                plan_id=2, user_id=USER_ID, expiration=datetime(2099, 1, 1)
            )
        )
        session.commit()

    storage_info = get_user_storage_usage(USER_ID)

    # Taken while the row lookup holds the pool's one connection, the plan
    # lookup times out and the free quota applies
    assert storage_info["storage_quota_bytes"] == StorageQuota.PREMIUM * StorageSize.GB


# The workspace-id lookup and the user lookup of the S3 scan
WORKSPACE_IDS_QUERY = "FROM workspaces"
USER_QUERY = "FROM users"


@contextmanager
def _failing(engine, query: str):
    """Statements containing `query` fail as a pool checkout timeout does."""

    def fail(conn, cursor, statement, *args):
        if query in statement:
            raise PoolTimeoutError("QueuePool limit reached, connection timed out")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        yield
    finally:
        event.remove(engine, "before_cursor_execute", fail)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "storage_type, query",
    [
        (RemoteStorageType.MOCK, WORKSPACE_IDS_QUERY),
        (RemoteStorageType.S3, WORKSPACE_IDS_QUERY),
        (RemoteStorageType.S3, USER_QUERY),
    ],
    ids=["local-workspaces", "s3-workspaces", "s3-user"],
)
async def test_a_failed_scan_lookup_keeps_the_stored_usage(
    stale_engine, s3_listing, storage_type, query
):
    with patch.object(
        RemoteStorageType, "get_activated_type", return_value=storage_type
    ), _failing(stale_engine, query):
        async with asgi_client(_app(asyncio.Event())) as client:
            response = await client.get("/storage-limit-alerts/usage")

    assert response.status_code == 200
    assert response.json()["storage_usage_bytes"] == USAGE_BYTES
    assert _stored_usage(stale_engine) == USAGE_BYTES


@pytest.mark.asyncio
async def test_the_refresh_answers_busy_and_keeps_the_usage_on_a_lookup_timeout(
    engine, s3_listing
):
    with _failing(engine, WORKSPACE_IDS_QUERY):
        async with asgi_client(_app(asyncio.Event())) as client:
            response = await client.post("/storage-limit-alerts/refresh")

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "5"
    assert _stored_usage(engine) == USAGE_BYTES


@pytest.mark.asyncio
async def test_the_workspace_refresh_answers_busy_on_a_workspace_lookup_timeout(
    engine,
):
    app = FastAPI()
    app.include_router(workspace.router)
    app.dependency_overrides[get_current_user] = _user
    with _failing(engine, WORKSPACE_IDS_QUERY):
        async with asgi_client(app) as client:
            response = await client.post("/workspaces/refresh-storage")

    assert response.status_code == 429
    assert _stored_usage(engine) == USAGE_BYTES


@pytest.mark.asyncio
async def test_the_reconciliation_keeps_the_usage_on_a_failed_lookup(stale_engine):
    with patch.object(
        RemoteStorageType, "get_activated_type", return_value=RemoteStorageType.MOCK
    ), _failing(stale_engine, WORKSPACE_IDS_QUERY):
        reconciled = await StorageReconciliationJob.reconcile_user_storage(USER_ID)

    assert reconciled is False
    assert _stored_usage(stale_engine) == USAGE_BYTES
