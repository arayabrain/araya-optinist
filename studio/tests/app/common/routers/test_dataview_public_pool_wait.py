"""The public DataView path on a one-connection pool.

An anonymous visitor's requests (the record list, a published record's
reproduce, and the output dependencies' public check and bucket lookup) wait
for a pooled connection in a worker thread, so another request is answered
meanwhile (db_pool_probe). The queries run on the real pool; the record's
local files and the workflow read are stubbed.
"""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anyio import to_thread
from fastapi import Depends, FastAPI
from fastapi_pagination import add_pagination
from sqlmodel import Session

from studio.app.common.core.auth.auth_dependencies import (
    get_current_user_with_dataview_outputs_check,
    get_outputs_remote_bucket_name,
)
from studio.app.common.core.dataview.dataview_services import DataviewService
from studio.app.common.models import Organization, User, Workspace
from studio.app.common.models.experiment import ExperimentRecord
from studio.app.common.routers import dataview
from studio.app.common.schemas.dataview import LocalSyncStatus, PublishStatus
from studio.app.common.schemas.workflow import WorkflowWithResults
from studio.tests.app.common.db_pool_probe import (
    add_hold_and_health_routes,
    answered_while_waiting,
    asgi_client,
    one_connection_pool,
)
from studio.tests.app.common.sqlite_harness import sqlite_tables

WORKSPACE_ID = 1
UNIQUE_ID = "abcd1234"
OWNER_BUCKET = "owner-bucket"
OUTPUT_PATH = f"{WORKSPACE_ID}/{UNIQUE_ID}/node/a.png"
PUBLIC_HEADERS = {DataviewService.DATAVIEW_PUBLIC_REQUEST_KEY: "1"}


@pytest.fixture
def engine(tmp_path):
    tables = [
        Organization.__table__,
        User.__table__,
        Workspace.__table__,
        ExperimentRecord.__table__,
    ]
    with one_connection_pool(tmp_path / "pool.db", timeout=3) as engine, sqlite_tables(
        engine, tables
    ):
        with Session(engine) as session:
            session.add(Organization(id=1, name="Organization"))
            session.add(
                User(
                    id=1,
                    organization_id=1,
                    uid="uid-1",
                    name="Owner",
                    email="owner@example.com",
                    attributes={"remote_bucket_name": OWNER_BUCKET},
                    active=True,
                )
            )
            session.add(
                Workspace(
                    id=WORKSPACE_ID,
                    user_id=1,
                    name="ws",
                    deleted=False,
                    input_data_usage=0,
                )
            )
            session.add(
                ExperimentRecord(
                    workspace_id=WORKSPACE_ID,
                    uid=UNIQUE_ID,
                    name="published",
                    data_usage=0,
                    success=True,
                    analyzed_at=datetime.now(timezone.utc),
                    publish_status=PublishStatus.on.value,
                    local_sync_status=LocalSyncStatus.synced.value,
                )
            )
            session.commit()
        yield engine


def _app(released: asyncio.Event) -> FastAPI:
    app = FastAPI()
    app.include_router(dataview.public_router)

    # The outputs router reads files; these routes keep one dependency each
    @app.get("/api/visualizations/thumbnail/{path:path}")
    async def public_check(
        _=Depends(get_current_user_with_dataview_outputs_check),
    ):
        return {}

    @app.get("/api/visualizations/image/{path:path}")
    async def bucket(bucket_name: str = Depends(get_outputs_remote_bucket_name)):
        return {"bucket": bucket_name}

    add_hold_and_health_routes(app, released)
    add_pagination(app)
    return app


def _reproduce_result() -> WorkflowWithResults:
    return WorkflowWithResults(
        workspace_id=str(WORKSPACE_ID),
        unique_id=UNIQUE_ID,
        name="published",
        started_at="",
        finished_at=None,
        success="success",
        hasNWB=False,
        function={},
        nodeDict={},
        edgeDict={},
    )


async def _send_and_probe(engine, send_path, headers=None):
    released = asyncio.Event()
    async with asgi_client(_app(released)) as client:
        return await answered_while_waiting(
            client, engine, released, lambda: client.get(send_path, headers=headers)
        )


def _assert_answered_while_waiting(health, waiting, response):
    assert health.status_code == 200
    assert waiting
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_public_record_list(engine):
    health, waiting, response = await _send_and_probe(engine, "/api/public/dataview")

    _assert_answered_while_waiting(health, waiting, response)
    assert [item["uid"] for item in response.json()["items"]] == [UNIQUE_ID]


@pytest.mark.asyncio
async def test_the_public_record_list_leaves_sync_routes_their_threads(engine):
    app = _app(asyncio.Event())

    @app.get("/sync")
    def sync():
        return {}

    limiter = to_thread.current_default_thread_limiter()
    default_tokens = limiter.total_tokens
    # One thread for this event loop's sync routes and dependencies
    limiter.total_tokens = 1
    try:
        async with asgi_client(app) as client:
            with engine.connect():
                listing = asyncio.create_task(client.get("/api/public/dataview"))
                # Long enough for the list to reach the pool wait
                await asyncio.sleep(0.2)
                sync_response = await asyncio.wait_for(client.get("/sync"), timeout=1)
                was_waiting = not listing.done()
            response = await listing
    finally:
        limiter.total_tokens = default_tokens

    assert sync_response.status_code == 200
    assert was_waiting
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_public_output_check(engine):
    health, waiting, response = await _send_and_probe(
        engine, f"/api/visualizations/thumbnail/{OUTPUT_PATH}", headers=PUBLIC_HEADERS
    )

    _assert_answered_while_waiting(health, waiting, response)


@pytest.mark.asyncio
async def test_the_public_output_bucket_lookup(engine):
    health, waiting, response = await _send_and_probe(
        engine, f"/api/visualizations/image/{OUTPUT_PATH}", headers=PUBLIC_HEADERS
    )

    _assert_answered_while_waiting(health, waiting, response)
    assert response.json() == {"bucket": OWNER_BUCKET}


@pytest.mark.asyncio
async def test_the_public_reproduce(engine):
    with patch.object(
        dataview.RemoteSyncStatusFileUtil,
        "check_sync_status_unsynced",
        return_value=False,
    ), patch.object(
        dataview.PublishValidator,
        "validate_for_display",
        return_value=MagicMock(is_displayable=True),
    ), patch.object(
        dataview,
        "reproduce_experiment",
        new=AsyncMock(return_value=_reproduce_result()),
    ):
        health, waiting, response = await _send_and_probe(
            engine,
            f"/api/public/dataview/workflow/reproduce/{WORKSPACE_ID}/{UNIQUE_ID}",
        )

    _assert_answered_while_waiting(health, waiting, response)
    assert response.json()["unique_id"] == UNIQUE_ID
