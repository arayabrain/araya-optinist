"""A real one-connection SQLAlchemy pool behind get_db, for tests that prove a
pool wait does not hold the event loop.

A request returns its connection in get_db's teardown, which FastAPI starts
from the event loop. So a pool wait on the loop cannot be ended by any
request's teardown, and a test tells a wait on the loop from one in a thread by
whether another request is answered meanwhile.
"""

import asyncio
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Awaitable, Callable, List
from unittest.mock import patch

import httpx
from anyio import to_thread
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import QueuePool

from studio.app.common.db.database import get_db

_AUTH_MODULE = "studio.app.common.core.auth.auth_dependencies"
# Both auth modes' headers, so the tests hold whichever AUTH_CONFIG selects
AUTH_HEADERS = {"Authorization": "Bearer token", "ExToken": "token"}
STUB_USER_ID = 1


def _user_row(db, uid):
    """Stands in for the user query, checking out a connection as it does."""
    db.execute(text("SELECT 1"))
    user = SimpleNamespace(
        id=STUB_USER_ID,
        uid=uid,
        name="User",
        email="user@example.com",
        organization=SimpleNamespace(id=1, name="Organization"),
        attributes={},
    )
    return user, 1, 0, None, 0, 0, None, None


@contextmanager
def stub_token_and_user_row():
    """Run the real get_current_user with only the token check and the user
    row stubbed."""
    with patch(
        f"{_AUTH_MODULE}.extract_uid_from_firebase_credential",
        return_value=("uid-1", None),
    ), patch(
        f"{_AUTH_MODULE}.extract_uid_from_jwt_token",
        return_value=("uid-1", None),
    ), patch(
        f"{_AUTH_MODULE}.__get_current_user_record", side_effect=_user_row
    ):
        yield


@contextmanager
def stub_firebase_verify(uid: str):
    """Stub only Firebase's token check; the token cache is emptied around it."""
    from studio.app.common.core.auth import auth_helper

    auth_helper._token_cache.clear()
    try:
        with patch.object(
            auth_helper.firebase_auth, "verify_id_token", return_value={"uid": uid}
        ):
            yield
    finally:
        auth_helper._token_cache.clear()


@contextmanager
def one_connection_pool(db_path, timeout: float):
    """Serve get_db and get_session from a SQLite file pool of one connection."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=timeout,
        connect_args={"check_same_thread": False},
    )
    try:
        with patch(
            "studio.app.common.db.database.get_cached_engine", return_value=engine
        ):
            yield engine
    finally:
        engine.dispose()


def asgi_client(app: FastAPI) -> httpx.AsyncClient:
    """Serves the app in-process, on the test's event loop."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def wait_until(condition, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        await asyncio.sleep(0.01)


@contextmanager
def statements_on_the_loop(engine):
    """Collect the SQL the engine runs on the calling thread, the event loop's."""
    loop_thread = threading.current_thread()
    on_loop = []

    def record(conn, cursor, statement, *args):
        if threading.current_thread() is loop_thread:
            on_loop.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield on_loop
    finally:
        event.remove(engine, "before_cursor_execute", record)


def add_hold_and_health_routes(app: FastAPI, released: asyncio.Event) -> None:
    """/hold keeps a pooled connection until released; /health needs none."""

    @app.get("/hold")
    async def hold(db=Depends(get_db)):
        # In a thread, so its statement is not one the loop ran
        await to_thread.run_sync(db.execute, text("SELECT 1"))
        await released.wait()

    @app.get("/health")
    async def health():
        return {}


@dataclass
class PoolWaitProbe:
    slept: float  # what a 0.2 s sleep on the loop took while the request waited
    health: httpx.Response
    waiting: bool  # the request was still waiting when /health was answered
    response: httpx.Response  # once the connection was returned
    on_loop: List[str]  # statements that ran on the event loop


async def answered_while_waiting(
    client: httpx.AsyncClient,
    engine,
    released: asyncio.Event,
    send: Callable[[], Awaitable[httpx.Response]],
) -> PoolWaitProbe:
    """Send a request while /hold keeps the pool's only connection."""
    with statements_on_the_loop(engine) as on_loop:
        hold = asyncio.create_task(client.get("/hold"))
        await wait_until(lambda: engine.pool.checkedout() == 1)

        request = asyncio.create_task(send())
        started = time.monotonic()
        # Long enough for the request to reach the pool wait
        await asyncio.sleep(0.2)
        slept = time.monotonic() - started
        health = await asyncio.wait_for(client.get("/health"), timeout=1)
        waiting = not request.done()

        released.set()
        response = await request
        await hold
    return PoolWaitProbe(slept, health, waiting, response, list(on_loop))


def assert_answered_while_waiting(probe: PoolWaitProbe, status_code: int = 200):
    """The loop kept running while the request waited, and ran none of its SQL.

    - A pool wait on the loop holds the sleep until the pool times out.
    - A lookup after the first runs once the connection is back, without a
      wait; only on_loop shows it ran on the loop.
    """
    # Not a test module, so pytest does not rewrite these: each says what failed
    assert probe.slept < 1, f"the event loop stalled for {probe.slept:.1f} s"
    assert probe.health.status_code == 200, f"/health: {probe.health.status_code}"
    assert probe.waiting, "the request ended before /health was answered"
    assert probe.response.status_code == status_code, probe.response.text
    assert probe.on_loop == [], f"statements on the event loop: {probe.on_loop}"
