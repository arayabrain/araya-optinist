"""A real one-connection SQLAlchemy pool behind get_db, for tests that prove a
pool wait does not hold the event loop.

A request returns its connection in get_db's teardown, which FastAPI starts
from the event loop. So a pool wait on the loop cannot be ended by any
request's teardown, and a test tells a wait on the loop from one in a thread by
whether another request is answered meanwhile.
"""

import asyncio
import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

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
