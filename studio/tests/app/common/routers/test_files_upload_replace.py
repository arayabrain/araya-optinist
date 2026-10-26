"""Uploads (create_file) and URL uploads (download) replace their file through a
temporary file beside it, so a reader that has the old file open (a running
workflow) keeps reading the old file, and a failed write leaves the old file in
place. A URL upload reads as done only once its file is in place."""
import asyncio
import errno
import io
import os
import stat
import threading
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import BackgroundTasks

from studio.app.common.routers import files
from studio.app.common.routers.files import _write_file_atomic

OLD, NEW = b"old movie " * 1000, b"new movie " * 10


def test_a_failed_write_leaves_the_old_file(tmp_path, monkeypatch):
    def copy_until_the_disk_is_full(source, dest):
        dest.write(source.read(10))
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(files.shutil, "copyfileobj", copy_until_the_disk_is_full)
    path = tmp_path / "movie.tif"
    path.write_bytes(OLD)
    with pytest.raises(OSError, match="No space left"):
        _write_file_atomic(str(path), io.BytesIO(NEW))
    assert path.read_bytes() == OLD
    assert os.listdir(tmp_path) == ["movie.tif"]


def test_a_long_file_name_still_works(tmp_path):
    """The temporary name does not grow with the target's (255-byte limit)."""
    path = tmp_path / ("m" * 240 + ".tif")
    _write_file_atomic(str(path), io.BytesIO(NEW))
    assert path.read_bytes() == NEW


def test_the_file_gets_the_default_permissions(tmp_path):
    """As a file open() creates; a mkstemp file would be 0600, private to the
    API's user."""
    umask = os.umask(0o022)
    try:
        path = tmp_path / "movie.tif"
        _write_file_atomic(str(path), io.BytesIO(NEW))
    finally:
        os.umask(umask)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.fixture
def input_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(files.DIRPATH, "INPUT_DIR", str(tmp_path))
    monkeypatch.setattr(files, "DOWNLOAD_STATUS", {})
    (tmp_path / "1").mkdir()
    return tmp_path / "1"


def test_the_upload_route_replaces_the_file_under_an_open_reader(input_dir):
    path = input_dir / "data.csv"
    path.write_bytes(OLD)
    with patch.object(
        files, "get_current_user_storage_usage", new=AsyncMock(return_value=0)
    ), patch.object(files, "get_user_storage_usage"), patch.object(
        files, "get_effective_quota_bytes", return_value=0
    ), patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=False
    ), patch.object(
        files.RemoteStorageController, "is_available", return_value=False
    ), open(
        path, "rb", buffering=0
    ) as reader:
        result = asyncio.run(
            files.create_file(
                workspace_id="1",
                filename="data.csv",
                background_tasks=BackgroundTasks(),
                file=Mock(file=io.BytesIO(NEW)),
                current_user=Mock(id=1),
                remote_bucket_name="bucket",
                db=Mock(),
            )
        )
        assert reader.read() == OLD
    assert result == {"file_path": "data.csv"}
    assert path.read_bytes() == NEW
    assert os.listdir(input_dir) == ["data.csv"]


def url_response(*chunks, content_length=None):
    def iter_content(chunk_size):
        for chunk in chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    headers = {} if content_length is None else {"content-length": str(content_length)}
    return Mock(headers=headers, iter_content=iter_content)


def record_status_at_replace(monkeypatch):
    """The download status each os.replace sees, i.e. just before the file lands."""
    at_replace = []
    real_replace = os.replace

    def replace(src, dst):
        at_replace.append(files.DOWNLOAD_STATUS.get(dst))
        real_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", replace)
    return at_replace


def test_a_url_upload_replaces_the_file_under_an_open_reader(input_dir):
    path = input_dir / "data.csv"
    path.write_bytes(OLD)
    with patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=False
    ), open(path, "rb", buffering=0) as reader:
        files.download(url_response(NEW[:50], NEW[50:]), "data.csv", "1")
        assert reader.read() == OLD
    assert path.read_bytes() == NEW
    assert os.listdir(input_dir) == ["data.csv"]
    # Without Content-Length the total is unknown; done still ends the upload
    assert files.DOWNLOAD_STATUS[str(path)].done


def test_a_url_upload_that_fails_midway_leaves_the_old_file(input_dir):
    path = input_dir / "data.csv"
    path.write_bytes(OLD)
    with patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=False
    ):
        files.download(
            url_response(NEW[:50], ConnectionError("connection reset")), "data.csv", "1"
        )
    status = files.DOWNLOAD_STATUS[str(path)]
    assert "connection reset" in status.error
    assert not status.done
    assert path.read_bytes() == OLD
    assert os.listdir(input_dir) == ["data.csv"]


def test_a_url_upload_is_done_only_once_the_file_is_in_place(input_dir, monkeypatch):
    """The frontend finishes on done and uses the file."""
    path = input_dir / "data.csv"
    at_replace = record_status_at_replace(monkeypatch)
    response = url_response(NEW[:50], NEW[50:], content_length=len(NEW))
    with patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=False
    ):
        files.download(response, "data.csv", "1", "id")

    (status,) = at_replace
    assert (status.current, status.total, status.done) == (len(NEW), len(NEW), False)
    finished = files.DOWNLOAD_STATUS[str(path)]
    assert (finished.current, finished.done, finished.upload_id) == (
        len(NEW),
        True,
        "id",
    )
    assert path.read_bytes() == NEW


def test_a_same_name_url_upload_does_not_read_as_done_before_its_file(
    input_dir, monkeypatch
):
    """A one-chunk download writes no progress before the replace, so the
    previous upload's done entry must be gone when the route returns."""
    path = input_dir / "data.csv"
    path.write_bytes(OLD)
    files.DOWNLOAD_STATUS[str(path)] = files.DownloadStatus(
        total=len(OLD), current=len(OLD), done=True, upload_id="earlier"
    )
    background_tasks = BackgroundTasks()
    response = url_response(NEW, content_length=len(NEW))
    with patch.object(files, "open_public_url", return_value=response):
        result = asyncio.run(
            files.download_file(
                workspace_id="1",
                file=files.DownloadFileRequest(url="https://example.com/data.csv"),
                background_tasks=background_tasks,
                db=Mock(),
            )
        )
    upload_id = result["upload_id"]
    assert upload_id != "earlier"
    queued = files.DOWNLOAD_STATUS[str(path)]
    assert (queued.done, queued.current, queued.upload_id) == (False, 0, upload_id)

    at_replace = record_status_at_replace(monkeypatch)
    task = background_tasks.tasks[0]
    with patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=False
    ):
        task.func(*task.args, **task.kwargs)
    (status,) = at_replace
    assert not status.done
    assert files.DOWNLOAD_STATUS[str(path)].done
    assert path.read_bytes() == NEW


def test_the_upload_routes_fetch_and_copy_off_the_event_loop(input_dir):
    """Blocking work on the loop would stall every request on that worker."""
    threads = {}

    def fetch(url):
        threads["fetch"] = threading.current_thread()
        return url_response(NEW, content_length=len(NEW))

    def copy(filepath, source):
        threads["copy"] = threading.current_thread()

    with patch.object(files, "open_public_url", side_effect=fetch), patch.object(
        files, "_write_file_atomic", side_effect=copy
    ), patch.object(
        files, "get_current_user_storage_usage", new=AsyncMock(return_value=0)
    ), patch.object(
        files, "get_user_storage_usage"
    ), patch.object(
        files, "get_effective_quota_bytes", return_value=0
    ), patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=False
    ), patch.object(
        files.RemoteStorageController, "is_available", return_value=False
    ):
        asyncio.run(
            files.download_file(
                workspace_id="1",
                file=files.DownloadFileRequest(url="https://example.com/data.csv"),
                background_tasks=BackgroundTasks(),
                db=Mock(),
            )
        )
        asyncio.run(
            files.create_file(
                workspace_id="1",
                filename="data.csv",
                background_tasks=BackgroundTasks(),
                file=Mock(file=io.BytesIO(NEW)),
                current_user=Mock(id=1),
                remote_bucket_name="bucket",
                db=Mock(),
            )
        )
    # asyncio.run runs the loop in this (the main) thread
    assert threads["fetch"] is not threading.main_thread()
    assert threads["copy"] is not threading.main_thread()


def test_the_status_route_answers_only_for_the_upload_asked_about(input_dir):
    """Another worker may still hold an earlier upload's done entry."""
    path = str(input_dir / "data.csv")
    files.DOWNLOAD_STATUS[path] = files.DownloadStatus(done=True, upload_id="earlier")

    def status(**kwargs):
        return asyncio.run(
            files.get_download_status(workspace_id="1", file_name="data.csv", **kwargs)
        )

    with pytest.raises(files.HTTPException) as raised:
        status(upload_id="this")
    assert raised.value.status_code == 404
    assert status(upload_id="earlier").done
    assert status().done  # a caller that sends no id
