"""Background tasks must not run on the request's database session.

FastAPI 0.104 tears get_db down only after the request's background tasks
finish. Two consequences are pinned here:

- A background task never receives a get_db session (on FastAPI >= 0.106 it
  would receive a closed one). It opens its own session_scope().
- An endpoint that queues long work releases the request session before it
  returns, so the run does not hold a pooled connection past idle timeout.
"""
import ast
import asyncio
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException

import studio.app
from studio.app.common.routers import files, run
from studio.app.common.schemas.files import DownloadFileRequest

APP_DIR = Path(studio.app.__file__).parent


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


def _add_task_session_args(tree):
    """Yield (lineno, name) for each session passed to background_tasks.add_task."""
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        sessions = _session_params(func) | {"db"}
        for node in ast.walk(func):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_task"
            ):
                continue
            values = node.args + [kw.value for kw in node.keywords]
            for value in values:
                if isinstance(value, ast.Name) and value.id in sessions:
                    yield node.lineno, value.id


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


def test_the_guard_detects_a_session_passed_to_add_task():
    source = """
async def endpoint(background_tasks, conn: Session = Depends(get_db)):
    background_tasks.add_task(work, conn, 1)
"""
    assert list(_add_task_session_args(ast.parse(source))) == [(3, "conn")]


# --------------------------------------------------------------------------
# Endpoints that queue long work release the request session
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
    ):
        runner_cls.create_workflow_unique_id.return_value = "uid"
        yield runner


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
