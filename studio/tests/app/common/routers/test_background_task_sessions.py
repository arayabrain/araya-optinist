"""Background tasks must not run on the request's database session.

FastAPI 0.104 tears get_db down only after the request's background tasks
finish. Three consequences are pinned here:

- A background task never receives a get_db session (on FastAPI >= 0.106 it
  would receive a closed one). It opens its own session_scope().
- An endpoint that depends on get_db and queues long work releases the request
  session before it returns, so the task does not hold a pooled connection
  past the proxy's idle timeout. Every such endpoint is either released or
  listed as queuing only short work.
- The release has the intended effect on the pinned FastAPI: no connection is
  checked out while the task runs.

Limits of the static guard: it finds a session passed to add_task by name,
inside a partial or lambda, or as an attribute (self.db). It does not find a
session bound to an unannotated name other than `db` or `session`, for
example `s = get_session()`.

Limits of the classification: it keys on BackgroundTasks, so it does not see
the other hold of the same shape. The 0.104 teardown also waits for the
response body, so a get_db endpoint returning a FileResponse (the NWB, output
and config downloads) holds its connection for the whole file send. It also
pins endpoint names, not durations: an endpoint listed as short-task that later
queues long work stays green until someone re-checks the list.
"""
import ast
import asyncio
import inspect
import re
import textwrap
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

import studio.app
from studio.app.common.core.auth.auth_dependencies import (
    get_current_user,
    get_user_remote_bucket_name,
)
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageLockError,
)
from studio.app.common.core.workspace.workspace_dependencies import is_workspace_owner
from studio.app.common.db.database import get_db
from studio.app.common.routers import (
    algolist,
    auth,
    dataview,
    experiment,
    files,
    internal,
    log_report,
    logs,
    outputs,
    params,
    registrations,
    run,
    storage_limit_alerts,
    subscriptions,
    users_admin,
    users_me,
    users_search,
    workflow,
    workspace,
)
from studio.app.common.schemas.files import DownloadFileRequest
from studio.app.optinist.routers import hdf5, mat, nwb, roi
from studio.tests.app.common.dependency_tree import depends_on

APP_DIR = Path(studio.app.__file__).parent

ROUTER_MODULES = [
    algolist,
    auth,
    dataview,
    experiment,
    files,
    internal,
    log_report,
    logs,
    outputs,
    params,
    registrations,
    run,
    storage_limit_alerts,
    subscriptions,
    users_admin,
    users_me,
    users_search,
    workflow,
    workspace,
    hdf5,
    mat,
    nwb,
    roi,
]

# Endpoints that depend on get_db and queue long work: each releases the
# request session before it returns. Keyed by (router module, endpoint).
RELEASING_ENDPOINTS = {
    ("run", "run"),
    ("run", "run_id"),
    ("files", "create_file"),
    ("files", "download_file"),
    ("outputs", "sync_visualization_files"),
    ("internal", "sync_user_experiments"),
}

# Endpoints that depend on get_db but queue only short work, so holding the
# session until teardown costs no more than the request itself. The list pins
# names, not durations: re-check an entry when its background work changes.
SHORT_TASK_ENDPOINTS = {
    ("files", "sync_input_file"): "uploads the input's metadata JSON",
    ("files", "delete_file"): "workspace usage (folder size)",
    ("run", "run_result"): "experiment usage (folder size)",
    ("run", "apply_filter"): "experiment usage (folder size)",
}


# --------------------------------------------------------------------------
# Static guard: no add_task call passes a request session
# --------------------------------------------------------------------------


def _is_get_db_dependency(default) -> bool:
    return (
        isinstance(default, ast.Call)
        and getattr(default.func, "id", None) == "Depends"
        and any(getattr(arg, "id", None) == "get_db" for arg in default.args)
    )


def _is_session_annotation(annotation) -> bool:
    return annotation is not None and "Session" in ast.unparse(annotation)


def _session_params(func) -> set:
    """Parameter names of `func` that hold a database session."""
    args = func.args
    positional = args.posonlyargs + args.args
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    pairs = list(zip(positional, defaults)) + list(
        zip(args.kwonlyargs, args.kw_defaults)
    )
    return {
        arg.arg
        for arg, default in pairs
        if _is_session_annotation(arg.annotation) or _is_get_db_dependency(default)
    }


def _session_refs(value, sessions):
    """Session names referenced anywhere in an add_task argument."""
    for node in ast.walk(value):
        if isinstance(node, ast.Name) and node.id in sessions:
            yield node.id
        elif isinstance(node, ast.Attribute) and node.attr in sessions:
            yield ast.unparse(node)


def _add_task_session_args(tree):
    """Yield (lineno, name) for each session passed to background_tasks.add_task."""
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        sessions = _session_params(func) | {"db", "session"}
        for node in ast.walk(func):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_task"
            ):
                continue
            for value in node.args + [kw.value for kw in node.keywords]:
                for name in _session_refs(value, sessions):
                    yield node.lineno, name


def test_no_background_task_receives_a_request_session():
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        offenders += [
            f"{path.relative_to(APP_DIR)}:{lineno} passes '{name}'"
            for lineno, name in _add_task_session_args(tree)
        ]
    assert offenders == [], (
        "background tasks must open their own session_scope() instead of "
        f"receiving the request's session: {offenders}"
    )


@pytest.mark.parametrize(
    "call, expected",
    [
        ("background_tasks.add_task(work, conn, 1)", "conn"),
        ("background_tasks.add_task(partial(work, conn))", "conn"),
        ("background_tasks.add_task(lambda: work(conn))", "conn"),
        ("background_tasks.add_task(work, session=conn)", "conn"),
        ("background_tasks.add_task(work, self.db)", "self.db"),
    ],
)
def test_the_guard_detects_a_session_passed_to_add_task(call, expected):
    source = (
        "async def endpoint(background_tasks, conn: Session = Depends(get_db)):\n"
        f"    {call}\n"
    )
    assert list(_add_task_session_args(ast.parse(source))) == [(2, expected)]


# --------------------------------------------------------------------------
# Every get_db endpoint that queues work is classified
# --------------------------------------------------------------------------


def _takes_background_tasks(dependant) -> bool:
    return dependant.background_tasks_param_name is not None or any(
        _takes_background_tasks(d) for d in dependant.dependencies
    )


def _module_routers(module):
    return [v for v in vars(module).values() if isinstance(v, APIRouter)]


def _get_db_routes_with_background_tasks():
    return {
        (module.__name__.rsplit(".", 1)[-1], route.name): route
        for module in ROUTER_MODULES
        for router in _module_routers(module)
        for route in router.routes
        if isinstance(route, APIRoute)
        and _takes_background_tasks(route.dependant)
        and depends_on(route.dependant, get_db)
    }


def test_every_get_db_endpoint_with_background_tasks_is_classified():
    """Fails when a new endpoint holds a get_db session across background work,
    so the release question is asked for it."""
    actual = set(_get_db_routes_with_background_tasks())
    known = RELEASING_ENDPOINTS | set(SHORT_TASK_ENDPOINTS)
    assert actual == known, (
        f"unclassified: {sorted(actual - known)}; stale: {sorted(known - actual)}. "
        "Release the request session (release_request_session) before queuing "
        "long work, or list the endpoint in SHORT_TASK_ENDPOINTS with the reason"
    )


def _calls(func, name) -> bool:
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return any(
        isinstance(node, ast.Call) and getattr(node.func, "id", None) == name
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize("key", sorted(RELEASING_ENDPOINTS), ids=".".join)
def test_releasing_endpoints_call_release_request_session(key):
    """A label check: the endpoint contains the call. Whether the call happens
    on each path is pinned by the release tests below."""
    route = _get_db_routes_with_background_tasks()[key]
    assert _calls(route.endpoint, "release_request_session")


def test_router_module_list_is_complete():
    listed = {Path(m.__file__).resolve() for m in ROUTER_MODULES}
    on_disk = {
        p.resolve()
        for p in APP_DIR.glob("*/routers/*.py")
        if p.name != "__init__.py" and re.search(r"=\s*APIRouter\(", p.read_text())
    }
    assert on_disk - listed == set(), f"add to ROUTER_MODULES: {on_disk - listed}"


# --------------------------------------------------------------------------
# Effect on the pinned FastAPI: no connection is held while the task runs
# --------------------------------------------------------------------------


@contextmanager
def _run_stubs(runner_raises=None):
    runner = Mock()
    if runner_raises:
        runner.run_workflow.side_effect = runner_raises
    with patch.object(run, "_check_storage_quota", new=AsyncMock()), patch.object(
        run, "ensure_structure_caches", new=AsyncMock()
    ), patch.object(run, "validate_input_edges"), patch.object(
        run, "WorkflowRunner", return_value=runner
    ) as runner_cls, patch.object(
        run.RemoteStorageController, "is_available", return_value=False
    ), patch.object(
        run.RemoteSyncLockFileUtil, "check_sync_lock_file"
    ), patch.object(
        run, "get_current_user_storage_usage", new=AsyncMock()
    ):
        runner_cls.create_workflow_unique_id.return_value = "uid"
        yield runner


def test_run_holds_no_connection_while_its_task_runs():
    engine = create_engine(
        "sqlite://",
        poolclass=QueuePool,
        connect_args={"check_same_thread": False},
    )
    make_session = sessionmaker(bind=engine)

    def counting_get_db():
        db = make_session()
        try:
            yield db
        finally:
            db.close()

    def querying_guard(db=Depends(get_db)):
        db.execute(text("select 1"))

    seen = {}

    def record(key):
        seen[key] = engine.pool.checkedout()

    app = FastAPI()
    app.include_router(run.router)

    # Control: a guarded endpoint that does not release the session
    @app.post("/held/{workspace_id}", dependencies=[Depends(is_workspace_owner)])
    def held(background_tasks: BackgroundTasks):
        background_tasks.add_task(record, "held")

    app.dependency_overrides = {
        get_db: counting_get_db,
        is_workspace_owner: querying_guard,
        get_current_user: lambda: Mock(id=1),
        get_user_remote_bucket_name: lambda: "bucket",
    }

    with _run_stubs() as runner:
        runner.run_workflow.side_effect = lambda tasks: tasks.add_task(record, "run")
        client = TestClient(app)
        assert client.post("/held/1").status_code == 200
        assert client.post("/run/1", json={"forceRunList": []}).status_code == 200

    assert seen["held"] == 1, (
        "the control no longer holds its connection during the task: FastAPI now "
        "tears get_db down before background tasks, so release_request_session "
        "may be removable"
    )
    assert seen["run"] == 0
    assert engine.pool.checkedout() == 0


# --------------------------------------------------------------------------
# Endpoints that queue long work release the request session
# --------------------------------------------------------------------------


def _call_run(endpoint, db, background_tasks):
    kwargs = dict(
        workspace_id="1",
        runItem=Mock(),
        background_tasks=background_tasks,
        remote_bucket_name="bucket",
        current_user=Mock(id=1),
        db=db,
    )
    if endpoint is run.run_id:
        kwargs["uid"] = "uid"
    return asyncio.run(endpoint(**kwargs))


@pytest.mark.parametrize("endpoint", [run.run, run.run_id])
def test_run_releases_the_request_session_before_the_run(endpoint):
    db = Mock()
    background_tasks = BackgroundTasks()

    def queue_run(tasks):
        # The session is still open while the run is queued ...
        assert not db.close.called
        tasks.add_task(Mock())

    with _run_stubs() as runner:
        runner.run_workflow.side_effect = queue_run
        assert _call_run(endpoint, db, background_tasks) == "uid"

    # ... and released by the time the response is sent, before any task runs
    db.close.assert_called_once()
    assert background_tasks.tasks


@pytest.mark.parametrize("endpoint", [run.run, run.run_id])
def test_run_releases_the_request_session_on_failure(endpoint):
    db = Mock()
    with _run_stubs(runner_raises=RuntimeError("boom")):
        with pytest.raises(HTTPException):
            _call_run(endpoint, db, BackgroundTasks())
    db.close.assert_called_once()


def test_download_file_releases_the_request_session():
    db = Mock()
    background_tasks = BackgroundTasks()
    with patch.object(files, "create_directory"), patch.object(files.requests, "get"):
        result = asyncio.run(
            files.download_file(
                workspace_id="1",
                file=DownloadFileRequest(url="https://example.com/data.csv"),
                background_tasks=background_tasks,
                db=db,
            )
        )
    assert result == {"file_name": "data.csv"}
    db.close.assert_called_once()
    task = background_tasks.tasks[0]
    assert task.func is files.download
    assert db not in task.args


def test_download_file_releases_the_request_session_on_failure():
    db = Mock()
    background_tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            files.download_file(
                workspace_id="1",
                file=DownloadFileRequest(url="https://example.com/data.exe"),
                background_tasks=background_tasks,
                db=db,
            )
        )
    assert raised.value.status_code == 400
    db.close.assert_called_once()
    assert background_tasks.tasks == []


@contextmanager
def _sync_visualization_stubs(reader_raises=None):
    reader = MagicMock()
    reader.__aenter__.return_value.download_experiment = AsyncMock(return_value=True)
    if reader_raises:
        reader.__aenter__.side_effect = reader_raises
    with patch.object(
        outputs.RemoteStorageController, "is_available", return_value=True
    ), patch.object(
        outputs.RemoteSyncStatusFileUtil,
        "check_sync_status_unsynced",
        return_value=True,
    ), patch.object(
        outputs, "RemoteStorageReader", return_value=reader
    ), patch.object(
        outputs.SmkUtils, "get_datatypes_inputs", return_value=[]
    ):
        yield


def _call_sync_visualization(db, background_tasks):
    return asyncio.run(
        outputs.sync_visualization_files(
            workspace_id="1",
            unique_id="uid",
            background_tasks=background_tasks,
            remote_bucket_name="bucket",
            db=db,
        )
    )


def test_sync_visualization_files_releases_the_request_session():
    db = Mock()
    background_tasks = BackgroundTasks()
    with _sync_visualization_stubs():
        assert _call_sync_visualization(db, background_tasks) is True
    db.close.assert_called_once()
    assert background_tasks.tasks[0].func is outputs._background_full_sync


def test_sync_visualization_files_releases_the_request_session_on_failure():
    db = Mock()
    with _sync_visualization_stubs(reader_raises=RemoteStorageLockError("1", "uid")):
        with pytest.raises(HTTPException):
            _call_sync_visualization(db, BackgroundTasks())
    db.close.assert_called_once()


def test_sync_user_experiments_releases_the_request_session():
    db = Mock()
    background_tasks = BackgroundTasks()
    with patch.dict(internal._rate_limit_cache, clear=True), patch.object(
        internal, "_get_user_remote_bucket_name", return_value="bucket"
    ):
        result = asyncio.run(
            internal.sync_user_experiments(
                user_id=1, background_tasks=background_tasks, db=db, _=None
            )
        )
    assert result["status"] == "sync_initiated"
    db.close.assert_called_once()
    task = background_tasks.tasks[0]
    assert task.func is internal._download_experiments_for_user
    assert db not in task.args


def test_sync_user_experiments_queues_nothing_for_an_unknown_user():
    """The 404 path queues no work, so the session is left to teardown."""
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    background_tasks = BackgroundTasks()
    with patch.dict(internal._rate_limit_cache, clear=True):
        with pytest.raises(HTTPException) as raised:
            asyncio.run(
                internal.sync_user_experiments(
                    user_id=1, background_tasks=background_tasks, db=db, _=None
                )
            )
    assert raised.value.status_code == 404
    assert background_tasks.tasks == []


def _queued_usage_updates(background_tasks):
    return [
        task.args
        for task in background_tasks.tasks
        if task.func is files._update_workspace_data_usage
    ]


def _call_create_file(db, background_tasks, over_quota=False):
    with patch.object(
        files,
        "get_current_user_storage_usage",
        new=AsyncMock(return_value=2 if over_quota else 0),
    ), patch.object(files, "get_user_storage_usage"), patch.object(
        files, "get_effective_quota_bytes", return_value=1 if over_quota else 0
    ), patch.object(
        files, "create_directory"
    ), patch(
        "studio.app.common.routers.files.open", create=True
    ), patch.object(
        files, "shutil"
    ), patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=True
    ), patch.object(
        files.RemoteStorageController, "is_available", return_value=False
    ):
        return asyncio.run(
            files.create_file(
                workspace_id="1",
                filename="data.csv",
                background_tasks=background_tasks,
                file=Mock(),
                current_user=Mock(id=1),
                remote_bucket_name="bucket",
                db=db,
            )
        )


def test_create_file_releases_the_request_session():
    db = Mock()
    background_tasks = BackgroundTasks()
    assert _call_create_file(db, background_tasks) == {"file_path": "data.csv"}
    db.close.assert_called_once()
    # Both tasks are still queued: the usage update and the live storage refresh
    assert _queued_usage_updates(background_tasks) == [("1",)]
    assert len(background_tasks.tasks) == 2
    assert all(db not in task.args for task in background_tasks.tasks)


def test_create_file_releases_the_request_session_on_failure():
    db = Mock()
    with pytest.raises(HTTPException) as raised:
        _call_create_file(db, BackgroundTasks(), over_quota=True)
    assert raised.value.status_code == 403
    db.close.assert_called_once()


# --------------------------------------------------------------------------
# Background usage updates open their own session
# --------------------------------------------------------------------------


def test_download_updates_usage_in_its_own_session(tmp_path):
    own_session = Mock()

    @contextmanager
    def fake_session_scope():
        yield own_session

    response = Mock(headers={"content-length": "3"})
    response.iter_content.return_value = [b"abc"]
    with patch.object(
        files, "join_filepath", return_value=str(tmp_path / "data.csv")
    ), patch.object(files, "session_scope", new=fake_session_scope), patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=True
    ), patch.object(
        files.WorkspaceDataCapacityService, "update_workspace_data_usage"
    ) as update:
        files.download(response, "data.csv", "1")
    update.assert_called_once_with(own_session, "1", auto_commit=False)


def test_delete_file_queues_the_usage_update():
    background_tasks = BackgroundTasks()
    with patch.object(files.os.path, "exists", return_value=True), patch.object(
        files.os, "remove"
    ), patch.object(files, "_remove_from_metadata_cache"), patch.object(
        files, "_update_and_upload_metadata", new=AsyncMock()
    ), patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=True
    ), patch.object(
        files.RemoteStorageController, "is_available", return_value=False
    ):
        assert (
            asyncio.run(
                files.delete_file(
                    workspace_id="1",
                    filename="data.csv",
                    background_tasks=background_tasks,
                    remote_bucket_name="bucket",
                )
            )
            is True
        )
    assert _queued_usage_updates(background_tasks) == [("1",)]
