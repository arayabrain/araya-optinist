"""The storage usage lookups on a one-connection pool: the storage routes the
top page and the run button send, and the quota check before a run or upload.

They read the user's storage row and subscription through session_scope. Those
lookups wait for a pooled connection in a worker thread, so another request is
answered meanwhile (db_pool_probe). A stale usage is recalculated and stored,
and the refresh scans and stores; no statement of either runs on the event
loop. The user and S3 are stubbed; the queries run on the real pool.
"""

import asyncio
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from sqlalchemy import update
from sqlmodel import Session, select

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.storage.remote_storage_controller import RemoteStorageType
from studio.app.common.core.subscription.constants import StorageQuota, StorageSize
from studio.app.common.core.utils.datetime_utils import get_current_datetime
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
from studio.app.common.routers import files, run, storage_limit_alerts
from studio.tests.app.common.db_pool_probe import (
    add_hold_and_health_routes,
    answered_while_waiting,
    asgi_client,
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
    """boto3's client: each listed prefix holds one object."""
    client = MagicMock()
    page = {"Contents": [{"Size": OBJECT_BYTES}], "IsTruncated": False}
    client.list_objects_v2.return_value = page
    client.get_paginator.return_value.paginate.side_effect = lambda **_: [page]
    with patch(
        "studio.app.common.core.cloud.s3_storage_monitor.boto3.client",
        return_value=client,
    ):
        yield client


def _stored_usage(engine) -> int:
    with Session(engine) as session:
        return session.exec(select(UserStorageUsage.storage_usage_bytes)).one()


def _app(released: asyncio.Event) -> FastAPI:
    app = FastAPI()
    app.include_router(storage_limit_alerts.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=USER_ID,
        name="User",
        email="user@example.com",
        remote_bucket_name="user-bucket",
    )
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
        health, waiting, response = await answered_while_waiting(
            client, engine, released, lambda: client.get(path)
        )

    assert health.status_code == 200
    assert waiting
    assert response.status_code == 200
    if expected is None:
        assert response.json() is None
    else:
        assert expected.items() <= response.json().items()


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
