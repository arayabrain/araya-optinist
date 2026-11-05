"""The storage usage lookups on a one-connection pool: the storage routes the
top page and the run button send, and the quota check before a run or upload.

They read the user's storage row and subscription through session_scope. Those
lookups wait for a pooled connection in a worker thread, so another request is
answered meanwhile (db_pool_probe). The user is stubbed; the queries run on the
real pool.
"""

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from sqlmodel import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.core.subscription.constants import StorageQuota, StorageSize
from studio.app.common.models import UserStorageUsage, UserSubscription
from studio.app.common.routers import files, run, storage_limit_alerts
from studio.tests.app.common.db_pool_probe import (
    add_hold_and_health_routes,
    answered_while_waiting,
    asgi_client,
    one_connection_pool,
)
from studio.tests.app.common.sqlite_harness import sqlite_tables

USER_ID = 1
USAGE_BYTES = 1024


@pytest.fixture
def engine(tmp_path):
    with one_connection_pool(tmp_path / "pool.db", timeout=3) as engine, sqlite_tables(
        engine, [UserStorageUsage.__table__, UserSubscription.__table__]
    ):
        # Fresh, so no route recalculates the usage from storage
        with Session(engine) as session:
            session.add(
                UserStorageUsage(
                    user_id=USER_ID,
                    storage_usage_bytes=USAGE_BYTES,
                    storage_quota_bytes=StorageQuota.FREE * StorageSize.GB,
                )
            )
            session.commit()
        yield engine


def _app(released: asyncio.Event) -> FastAPI:
    app = FastAPI()
    app.include_router(storage_limit_alerts.router)
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=USER_ID, name="User", email="user@example.com"
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
