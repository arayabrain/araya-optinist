"""get_db is a FastAPI dependency, so its signature is part of the API surface.

FastAPI turns every plain parameter of a dependency into a request parameter of
each operation that depends on it, directly or through another dependency such
as get_current_user. get_db therefore takes none, and always uses the cached
engine; the engine choice stays on get_session and session_scope, whose callers
are internal.

The allowlist test extends this to every dependency: a request parameter that
a dependency declares must be listed there, so a new one is a decision.
"""
import inspect
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from studio.__main_unit__ import app
from studio.app.common.db import database
from studio.app.common.db.database import get_db
from studio.tests.app.common.dependency_tree import (
    dependency_request_params,
    depends_on,
)

MODULE = "studio.app.common.db.database"

# Request parameters declared by dependencies rather than endpoints, keyed by
# (dependency, kind, name). Each one is meant to be caller-supplied.
INTENDED_DEPENDENCY_PARAMS = {
    (
        "studio.app.common.core.workspace.workspace_dependencies."
        "is_workspace_available",
        "query",
        "workspace_id",
    ),
    (
        "studio.app.common.core.workspace.workspace_dependencies.is_workspace_owner",
        "query",
        "workspace_id",
    ),
    ("studio.app.optinist.routers.roi.roi_filepath", "query", "workspace_id"),
    ("studio.app.common.routers.outputs.workspace_access", "query", "workspace_id"),
    (
        "studio.app.common.routers.internal.verify_internal_secret",
        "header",
        "x_internal_secret",
    ),
    ("studio.app.common.schemas.base.SortOptions", "query", "sort"),
    *(
        ("studio.app.common.schemas.dataview.DataviewRecordSearchOptions", "query", f)
        for f in (
            "name",
            "publish_status",
            "uid",
            "user_name",
            "workspace_id",
            "workspace_name",
        )
    ),
    *(
        ("studio.app.common.schemas.users.UserSearchOptions", "query", f)
        for f in ("email", "name")
    ),
}

# fastapi_pagination adds offset/limit to paginated routes in the app's lifespan,
# so they are present only after some test has started it. Allowed, never
# required.
PAGINATION_MODULE = "fastapi_pagination."
PAGINATION_PARAMS = {("query", "offset"), ("query", "limit")}


def _is_pagination_param(entry) -> bool:
    owner, kind, name = entry
    return owner.startswith(PAGINATION_MODULE) and (kind, name) in PAGINATION_PARAMS


def _schema_routes():
    return [
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.include_in_schema
    ]


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
    # Without operations on get_db the schema check below would pass vacuously.
    assert any(depends_on(route.dependant, get_db) for route in _schema_routes())
    assert _operations_exposing(app.openapi(), "use_cache") == []


def test_dependency_request_params_are_all_intended():
    found = set()
    for route in _schema_routes():
        found |= dependency_request_params(route.dependant)
    found = {entry for entry in found if not _is_pagination_param(entry)}
    assert found - INTENDED_DEPENDENCY_PARAMS == set(), (
        "A dependency declares a request parameter that is not listed. If callers "
        "are meant to supply it, add it to INTENDED_DEPENDENCY_PARAMS; otherwise "
        "remove it from the dependency's signature."
    )
    assert INTENDED_DEPENDENCY_PARAMS - found == set(), "Stale allowlist entries"


def test_a_request_sending_use_cache_is_served_on_the_cached_engine():
    cached = MagicMock(name="cached_engine")
    probe = FastAPI()

    @probe.get("/probe")
    def endpoint(db=Depends(get_db)):
        return {"bound_to_cached": db.get_bind() is cached}

    with patch(f"{MODULE}.get_cached_engine", return_value=cached), patch(
        f"{MODULE}.get_new_engine"
    ) as get_new:
        response = TestClient(probe).get("/probe", params={"use_cache": "false"})
    assert response.status_code == 200
    assert response.json() == {"bound_to_cached": True}
    get_new.assert_not_called()


def test_get_db_uses_the_cached_engine():
    cached = MagicMock(name="cached_engine")
    with patch(f"{MODULE}.get_cached_engine", return_value=cached) as get_cached, patch(
        f"{MODULE}.get_new_engine"
    ) as get_new:
        generator = get_db()
        try:
            assert next(generator).get_bind() is cached
        finally:
            generator.close()
    get_cached.assert_called_once()
    get_new.assert_not_called()


def test_get_db_raises_the_session_error_itself():
    with patch(f"{MODULE}.get_session", side_effect=RuntimeError("engine failed")):
        with pytest.raises(RuntimeError, match="engine failed"):
            next(get_db())


def test_get_session_keeps_the_engine_choice():
    """Internal callers can still ask for a fresh engine."""
    new = MagicMock(name="new_engine")
    with patch(f"{MODULE}.get_cached_engine") as get_cached, patch(
        f"{MODULE}.get_new_engine", return_value=new
    ):
        assert database.get_session(use_cache=False).get_bind() is new
    get_cached.assert_not_called()
