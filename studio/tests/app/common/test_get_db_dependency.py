"""get_db is a FastAPI dependency, so its signature is part of the API surface.

FastAPI turns every parameter of a dependency into a request parameter of each
operation that depends on it, directly or through get_current_user,
is_workspace_owner or is_workspace_available. get_db therefore takes none, and
always uses the cached engine; the engine choice stays on get_session and
session_scope, whose callers are internal.
"""
import inspect
from unittest.mock import MagicMock, patch

from studio.__main_unit__ import app
from studio.app.common.db import database
from studio.app.common.db.database import get_db

MODULE = "studio.app.common.db.database"


def _operations_exposing(schema: dict, name: str) -> list:
    hits = []
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for param in operation.get("parameters", []):
                if param["name"] == name:
                    hits.append(f"{method.upper()} {path} ({param['in']})")
    return hits


def test_get_db_takes_no_parameters():
    assert not inspect.signature(get_db).parameters


def test_no_operation_exposes_use_cache():
    assert _operations_exposing(app.openapi(), "use_cache") == []


def test_get_db_uses_the_cached_engine():
    cached = MagicMock(name="cached_engine")
    with patch(f"{MODULE}.get_cached_engine", return_value=cached) as get_cached, patch(
        f"{MODULE}.get_new_engine"
    ) as get_new:
        generator = get_db()
        db = next(generator)
        assert db.get_bind() is cached
        generator.close()
    get_cached.assert_called_once()
    get_new.assert_not_called()


def test_get_session_keeps_the_engine_choice():
    """Internal callers can still ask for a fresh engine."""
    new = MagicMock(name="new_engine")
    with patch(f"{MODULE}.get_cached_engine") as get_cached, patch(
        f"{MODULE}.get_new_engine", return_value=new
    ):
        assert database.get_session(use_cache=False).get_bind() is new
    get_cached.assert_not_called()
