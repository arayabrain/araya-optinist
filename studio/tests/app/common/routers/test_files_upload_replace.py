"""Uploads (create_file) and URL uploads (download) replace their file through a
temporary file beside it, so a reader that has the old file open (a running
workflow) keeps reading the old file, and a failed write leaves the old file in
place. Both routes then copy the file to remote storage under the file's lock,
and a URL upload reads as done only once that copy is there. Its status lives
on disk, where every worker of the instance can read it."""
import asyncio
import errno
import fcntl
import io
import os
import stat
import threading
import time
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import BackgroundTasks

from studio.app.common.routers import files
from studio.app.common.routers.files import _write_file_atomic
from studio.app.const import ACCEPT_FILE_EXT, MetadataCacheFile

OLD, NEW = b"old movie " * 1000, b"new movie " * 10
UPLOAD_ID = "0123456789abcdef" * 2
OTHER_ID = "fedcba9876543210" * 2


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
    (tmp_path / "1").mkdir()
    return tmp_path / "1"


def data_files(input_dir):
    """The workspace's files; .locks holds the locks and the upload statuses."""
    return [name for name in os.listdir(input_dir) if name != ".locks"]


def read_status(input_dir, upload_id=UPLOAD_ID):
    return files._read_download_status(
        str(input_dir / ".locks" / f"{upload_id}.download.json")
    )


def lock_is_held(input_dir, name):
    """flock is per open file description: a fresh fd sees another's lock."""
    os.makedirs(input_dir / ".locks", exist_ok=True)
    fd = os.open(input_dir / ".locks" / f"{name}.lock", os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


@contextmanager
def remote_writer(upload):
    """Remote storage whose upload_input_data is `upload(workspace_id, name)`."""
    remote = Mock(upload_input_data=AsyncMock(side_effect=upload))
    writer = MagicMock()
    writer.return_value.__aenter__.return_value = remote
    with patch.object(
        files.RemoteStorageController, "is_available", return_value=True
    ), patch.object(files, "RemoteStorageSimpleWriter", writer):
        yield remote.upload_input_data


@contextmanager
def no_quota_or_usage():
    with patch.object(
        files, "get_current_user_storage_usage", new=AsyncMock(return_value=0)
    ), patch.object(files, "get_user_storage_usage"), patch.object(
        files, "get_effective_quota_bytes", return_value=0
    ), patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=False
    ):
        yield


def upload(filename, data=NEW, remote=False):
    with no_quota_or_usage(), patch.object(
        files.RemoteStorageController, "is_available", return_value=remote
    ):
        return asyncio.run(
            files.create_file(
                workspace_id="1",
                filename=filename,
                background_tasks=BackgroundTasks(),
                file=Mock(file=io.BytesIO(data)),
                current_user=Mock(id=1),
                remote_bucket_name="bucket",
                db=Mock(),
            )
        )


def test_the_upload_route_replaces_the_file_under_an_open_reader(input_dir):
    path = input_dir / "data.csv"
    path.write_bytes(OLD)
    with open(path, "rb", buffering=0) as reader:
        result = upload("data.csv")
        assert reader.read() == OLD
    assert result == {"file_path": "data.csv"}
    assert path.read_bytes() == NEW
    assert data_files(input_dir) == ["data.csv"]


def url_response(*chunks, content_length=None, delay=0):
    def iter_content(chunk_size):
        for chunk in chunks:
            time.sleep(delay)
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    headers = {} if content_length is None else {"content-length": str(content_length)}
    return Mock(headers=headers, iter_content=iter_content)


def download(response, file_name="data.csv", upload_id=UPLOAD_ID, remote=False):
    with no_quota_or_usage(), patch.object(
        files.RemoteStorageController, "is_available", return_value=remote
    ):
        asyncio.run(files.download(response, file_name, "1", upload_id, "bucket"))


def record_status_at_replace(monkeypatch, input_dir, name="data.csv"):
    """The download status each os.replace of the file sees, i.e. just before
    the file lands. The status file's own replaces are not recorded."""
    at_replace = []
    real_replace = os.replace

    def replace(src, dst):
        if os.path.basename(dst) == name:
            at_replace.append(read_status(input_dir))
        real_replace(src, dst)

    monkeypatch.setattr(files.os, "replace", replace)
    return at_replace


def test_a_url_upload_replaces_the_file_under_an_open_reader(input_dir):
    path = input_dir / "data.csv"
    path.write_bytes(OLD)
    with open(path, "rb", buffering=0) as reader:
        download(url_response(NEW[:50], NEW[50:]))
        assert reader.read() == OLD
    assert path.read_bytes() == NEW
    assert data_files(input_dir) == ["data.csv"]
    # Without Content-Length the total is unknown; done still ends the upload
    assert read_status(input_dir).done


def test_a_url_upload_that_fails_midway_leaves_the_old_file(input_dir):
    path = input_dir / "data.csv"
    path.write_bytes(OLD)
    download(url_response(NEW[:50], ConnectionError("connection reset")))
    status = read_status(input_dir)
    assert "connection reset" in status.error
    assert not status.done
    assert path.read_bytes() == OLD
    assert data_files(input_dir) == ["data.csv"]


def test_a_url_upload_is_done_only_once_the_file_is_in_place(input_dir, monkeypatch):
    """The frontend finishes on done and uses the file."""
    path = input_dir / "data.csv"
    at_replace = record_status_at_replace(monkeypatch, input_dir)
    download(url_response(NEW[:50], NEW[50:], content_length=len(NEW)))

    (status,) = at_replace
    assert (status.current, status.total, status.done) == (len(NEW), len(NEW), False)
    finished = read_status(input_dir)
    assert (finished.current, finished.done, finished.upload_id) == (
        len(NEW),
        True,
        UPLOAD_ID,
    )
    assert path.read_bytes() == NEW


def test_the_status_is_a_file_every_worker_of_the_instance_can_read(input_dir):
    """Not a dict in the downloading worker's memory: a poll that reaches
    another uvicorn worker of the instance answers like the downloading one."""
    download(url_response(NEW, content_length=len(NEW)))
    status_file = input_dir / ".locks" / f"{UPLOAD_ID}.download.json"
    assert status_file.is_file()
    status = asyncio.run(files.get_download_status("1", UPLOAD_ID))
    assert (status.done, status.upload_id) == (True, UPLOAD_ID)
    assert not hasattr(files, "DOWNLOAD_STATUS")


def test_the_status_file_is_not_an_input_file(input_dir):
    """Under .locks, which the tree (and the cleanup job's S3 check) skip."""
    download(url_response(NEW, content_length=len(NEW)))
    tree = files.DirTreeGetter.get_tree("1", ACCEPT_FILE_EXT.CSV_EXT.value)
    assert [node.name for node in tree] == ["data.csv"]


def start_url_upload(url="https://example.com/data.csv", **overrides):
    background_tasks = BackgroundTasks()
    kwargs = dict(
        workspace_id="1",
        file=files.DownloadFileRequest(url=url),
        background_tasks=background_tasks,
        current_user=None,
        remote_bucket_name="bucket",
        db=Mock(),
    )
    kwargs.update(overrides)
    result = asyncio.run(files.download_file(**kwargs))
    return result["upload_id"], background_tasks.tasks[0]


def test_two_same_name_url_uploads_keep_separate_statuses(input_dir):
    """The status is keyed by upload id, so a still-running same-name upload
    cannot overwrite a newer one's status."""
    with patch.object(
        files, "open_url", return_value=url_response(NEW, content_length=len(NEW))
    ):
        first, first_task = start_url_upload()
        second, second_task = start_url_upload()
    assert first != second
    for upload_id in (first, second):
        queued = read_status(input_dir, upload_id)
        assert (queued.done, queued.current, queued.upload_id) == (
            False,
            0,
            upload_id,
        )

    with no_quota_or_usage(), patch.object(
        files.RemoteStorageController, "is_available", return_value=False
    ):
        asyncio.run(second_task())
    assert read_status(input_dir, second).done
    assert not read_status(input_dir, first).done
    assert (input_dir / "data.csv").read_bytes() == NEW


@pytest.mark.parametrize("standalone", [False, True])
def test_a_url_upload_is_limited_to_public_addresses_in_multi_user_mode(
    input_dir, monkeypatch, standalone
):
    """Standalone: the user owns the server, and may fetch from their LAN."""
    monkeypatch.setattr(files.MODE, "IS_STANDALONE", standalone)
    response = url_response(NEW, content_length=len(NEW))
    with patch.object(files, "open_url", return_value=response) as open_url:
        start_url_upload()
    assert open_url.call_args.kwargs["public_only"] is not standalone


def test_a_url_upload_is_refused_at_quota_before_fetching(input_dir):
    with patch.object(
        files, "get_current_user_storage_usage", new=AsyncMock(return_value=2)
    ), patch.object(files, "get_user_storage_usage"), patch.object(
        files, "get_effective_quota_bytes", return_value=1
    ), patch.object(
        files, "open_url"
    ) as open_url:
        with pytest.raises(files.HTTPException) as raised:
            start_url_upload(current_user=Mock(id=1))
    assert raised.value.status_code == 403
    open_url.assert_not_called()


def test_the_upload_routes_fetch_copy_and_parse_off_the_event_loop(input_dir):
    """Blocking work on the loop would stall every request on that worker."""
    threads = {}

    def record(name):
        def recorder(*args, **kwargs):
            threads[name] = threading.current_thread()

        return recorder

    def fetch(url, public_only):
        threads["fetch"] = threading.current_thread()
        return url_response(NEW, content_length=len(NEW))

    def stream(res, tmp_path, chunk_size, status_path, status):
        threads["stream"] = threading.current_thread()
        with open(tmp_path, "xb") as f:
            f.write(NEW)

    with patch.object(files, "open_url", side_effect=fetch), patch.object(
        files, "_stream_to_file", side_effect=stream
    ), patch.object(
        files, "_write_file_atomic", side_effect=record("copy")
    ), patch.object(
        files, "update_image_shape", side_effect=record("metadata")
    ), patch.object(
        files, "_update_workspace_data_usage", side_effect=record("usage")
    ), patch.object(
        files, "get_current_user_storage_usage", new=AsyncMock(return_value=0)
    ), patch.object(
        files, "get_user_storage_usage"
    ), patch.object(
        files, "get_effective_quota_bytes", return_value=0
    ), patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=True
    ), patch.object(
        files.RemoteStorageController, "is_available", return_value=False
    ):
        _, task = start_url_upload("https://example.com/data.tif")
        asyncio.run(task())
        asyncio.run(
            files.create_file(
                workspace_id="1",
                filename="data.tif",
                background_tasks=BackgroundTasks(),
                file=Mock(file=io.BytesIO(NEW)),
                current_user=Mock(id=1),
                remote_bucket_name="bucket",
                db=Mock(),
            )
        )
    # asyncio.run runs the loop in this (the main) thread
    assert set(threads) == {"fetch", "stream", "copy", "metadata", "usage"}
    assert all(thread is not threading.main_thread() for thread in threads.values())


def test_a_url_upload_copies_the_file_and_its_cache_to_remote_storage(input_dir):
    """The URL route used to be the one upload path that never reached S3."""
    seen = []

    def copy(workspace_id, name):
        seen.append(
            (
                name,
                (input_dir / name).is_file(),
                lock_is_held(input_dir, "data.tif"),
                read_status(input_dir).done,
            )
        )
        return True

    with remote_writer(copy):
        download(url_response(NEW, content_length=len(NEW)), "data.tif", remote=True)
    # Each copy sees its file in place, the data file locked and done not yet set
    assert seen == [
        ("data.tif", True, True, False),
        (MetadataCacheFile.IMAGE_SHAPE, True, True, False),
    ]
    assert read_status(input_dir).done


def test_the_upload_route_copies_the_file_and_its_cache_under_the_lock(input_dir):
    seen = []

    def copy(workspace_id, name):
        seen.append((name, (input_dir / name).is_file(), lock_is_held(input_dir, name)))
        return True

    with remote_writer(copy):
        upload("data.tif", remote=True)
    assert seen == [
        ("data.tif", True, True),
        (MetadataCacheFile.IMAGE_SHAPE, True, True),
    ]


def test_the_cache_is_updated_and_copied_in_one_hold_of_its_lock(input_dir):
    """A merged-tree fetch replaces the local cache with the remote copy when
    their sizes differ, and it takes the cache's lock to do so: released between
    the update and the copy, the new entry could be rolled back and never reach
    remote storage. The lock is not held during the file's own (long) copy."""
    cache = MetadataCacheFile.IMAGE_SHAPE
    events = []
    update_image_shape = files.update_image_shape

    def update(workspace_id, name, locked=True):
        events.append(("update", locked, lock_is_held(input_dir, cache)))
        update_image_shape(workspace_id, name, locked)

    def copy(workspace_id, name):
        events.append((f"copy {name}", lock_is_held(input_dir, cache)))
        return True

    with remote_writer(copy), patch.object(
        files, "update_image_shape", side_effect=update
    ):
        download(url_response(NEW, content_length=len(NEW)), "data.tif", remote=True)
    assert events == [
        ("copy data.tif", False),
        ("update", False, True),
        (f"copy {cache}", True),
    ]
    assert not lock_is_held(input_dir, cache)


def test_a_sync_of_the_same_name_waits_only_from_the_replace_to_the_copy(input_dir):
    """Locking the whole stream would make the visualization sync wait for
    minutes and then fall through, replacing the new file with the old copy."""
    seen = {}

    def chunks():
        seen["streaming"] = lock_is_held(input_dir, "data.csv")
        yield NEW

    def copy(workspace_id, name):
        seen["copying"] = lock_is_held(input_dir, "data.csv")
        return True

    response = Mock(headers={}, iter_content=lambda chunk_size: chunks())
    with remote_writer(copy):
        download(response, remote=True)
    assert seen == {"streaming": False, "copying": True}
    assert not lock_is_held(input_dir, "data.csv")


def test_a_failed_copy_to_remote_storage_is_reported(input_dir):
    """upload_input_data swallows the exception and returns False; a silent
    False leaves a file that only exists locally."""
    with remote_writer(lambda workspace_id, name: False):
        download(url_response(NEW, content_length=len(NEW)), remote=True)
    status = read_status(input_dir)
    assert "remote storage failed" in status.error
    assert not status.done

    with remote_writer(lambda workspace_id, name: False):
        with pytest.raises(files.HTTPException) as raised:
            upload("data.csv", remote=True)
    assert raised.value.status_code == 500


def test_a_long_file_name_still_uploads_by_url(input_dir):
    name = "m" * 240 + ".tif"
    download(url_response(NEW, content_length=len(NEW)), name)
    assert (input_dir / name).read_bytes() == NEW
    assert read_status(input_dir).done


def write_running_status(input_dir, age_seconds, upload_id=UPLOAD_ID):
    path = input_dir / ".locks" / f"{upload_id}.download.json"
    files._write_download_status(
        str(path), files.DownloadStatus(total=100, current=10, upload_id=upload_id)
    )
    then = time.time() - age_seconds
    os.utime(path, (then, then))


def test_a_status_the_downloading_worker_stopped_touching_reads_as_failed(
    input_dir,
):
    """A worker killed mid-download leaves a status that would poll forever."""
    write_running_status(input_dir, age_seconds=60)
    stale = asyncio.run(files.get_download_status("1", UPLOAD_ID))
    assert (stale.error, stale.done, stale.current) == ("The upload stopped", False, 10)

    write_running_status(input_dir, age_seconds=5)
    live = asyncio.run(files.get_download_status("1", UPLOAD_ID))
    assert (live.error, live.done, live.current) == (None, False, 10)


@pytest.mark.parametrize("heartbeat_seconds, stops", [(0.25, False), (100, True)])
def test_the_heartbeat_keeps_a_slow_download_alive(
    input_dir, monkeypatch, heartbeat_seconds, stops
):
    """Chunks can be minutes apart (the read timeout is per socket read, and
    a chunk is 1 MiB), and nothing streams during the lock wait or the copy."""
    monkeypatch.setattr(files, "DOWNLOAD_HEARTBEAT_SECONDS", heartbeat_seconds)
    monkeypatch.setattr(files, "DOWNLOAD_STALE_SECONDS", 1.0)
    monkeypatch.setattr(files, "DOWNLOAD_PROGRESS_WRITE_SECONDS", 100)
    write_running_status(input_dir, age_seconds=0)
    response = url_response(NEW[:10], NEW[10:], delay=2.0)

    async def poll():
        errors = []
        while True:
            await asyncio.sleep(0.1)
            status = await files.get_download_status("1", UPLOAD_ID)
            errors.append(status.error)
            if status.done or status.error:
                return errors

    async def run():
        _, errors = await asyncio.gather(
            files.download(response, "data.csv", "1", UPLOAD_ID), poll()
        )
        return errors

    with no_quota_or_usage(), patch.object(
        files.RemoteStorageController, "is_available", return_value=False
    ):
        errors = asyncio.run(run())
    assert ("The upload stopped" in errors) is stops


def test_the_status_route_answers_only_for_the_upload_asked_about(input_dir):
    write_running_status(input_dir, age_seconds=0, upload_id=OTHER_ID)
    with pytest.raises(files.HTTPException) as raised:
        asyncio.run(files.get_download_status("1", UPLOAD_ID))
    assert raised.value.status_code == 404
    assert asyncio.run(files.get_download_status("1", OTHER_ID)).current == 10


@pytest.mark.parametrize("upload_id", ["../x", UPLOAD_ID[:31], UPLOAD_ID + "0", ""])
def test_the_status_route_refuses_an_upload_id_that_is_not_one(input_dir, upload_id):
    with patch.object(files, "_read_download_status") as read:
        with pytest.raises(files.HTTPException) as raised:
            asyncio.run(files.get_download_status("1", upload_id))
    assert raised.value.status_code == 404
    read.assert_not_called()
