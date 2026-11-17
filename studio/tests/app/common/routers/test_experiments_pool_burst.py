"""GET /experiments under a burst of requests larger than the connection pool.

The route checks workspace access (is_workspace_available), then awaits the
remote storage while the request's session keeps that check's connection, as
it does when metadata is missing locally. A pool wait on the event loop would
stop that await, so the connection holder could not finish and return it.

The real auth dependency, workspace check, get_db and route run on a real
one-connection pool (db_pool_probe); the token check, the user row and the
remote storage are stubbed.
"""

import asyncio
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from sqlmodel import Session

from studio.app.common.core.auth.auth_dependencies import get_current_user
from studio.app.common.models import Workspace, WorkspacesShareUser
from studio.app.common.routers import experiment
from studio.tests.app.common.db_pool_probe import (
    AUTH_HEADERS,
    STUB_USER_ID,
    asgi_client,
    one_connection_pool,
    stub_token_and_user_row,
)
from studio.tests.app.common.sqlite_harness import sqlite_tables

# No experiment files exist for it, so the route downloads the metadata
WORKSPACE_ID = 987654
BURST = 10


class _SlowRemoteStorage:
    """Stands in for the remote storage reader: the download awaits."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def download_all_experiments_metas(self, workspace_ids):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_burst_of_experiment_lists_shares_one_connection(tmp_path):
    app = FastAPI()
    app.include_router(experiment.router, dependencies=[Depends(get_current_user)])

    @app.get("/health")
    async def health_endpoint():
        return {}

    with one_connection_pool(tmp_path / "pool.db", timeout=5) as engine, sqlite_tables(
        engine, [Workspace.__table__, WorkspacesShareUser.__table__]
    ), stub_token_and_user_row(), patch.object(
        experiment.RemoteStorageController, "is_available", return_value=True
    ), patch.object(
        experiment, "RemoteStorageSimpleReader", _SlowRemoteStorage
    ):
        with Session(engine) as session:
            session.add(
                Workspace(
                    id=WORKSPACE_ID, name="ws", user_id=STUB_USER_ID, deleted=False
                )
            )
            session.commit()

        async with asgi_client(app) as client:
            burst = [
                asyncio.create_task(
                    client.get(f"/experiments/{WORKSPACE_ID}", headers=AUTH_HEADERS)
                )
                for _ in range(BURST)
            ]
            await asyncio.sleep(0.05)
            health = await asyncio.wait_for(client.get("/health"), timeout=1)
            responses = await asyncio.gather(*burst)

    assert health.status_code == 200
    assert [r.status_code for r in responses] == [200] * BURST
    assert all(r.json() == {} for r in responses)
