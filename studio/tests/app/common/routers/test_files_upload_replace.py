"""Uploads (create_file) and URL uploads (download) replace their file through a
temporary file beside it, so a reader that has the old file open (a running
workflow) keeps reading the old file, and a failed write leaves the old file in
place."""
import asyncio
import io
import os
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import BackgroundTasks

from studio.app.common.routers import files
from studio.app.common.routers.files import _write_file_atomic

OLD, NEW = b"old movie " * 1000, b"new movie " * 10


def test_a_reader_of_the_old_file_keeps_reading_it(tmp_path):
    path = tmp_path / "movie.tif"
    path.write_bytes(OLD)
    with open(path, "rb", buffering=0) as reader:  # unbuffered: reads after replace
        head = reader.read(10)
        _write_file_atomic(str(path), io.BytesIO(NEW))
        assert head + reader.read() == OLD
    assert path.read_bytes() == NEW
    assert os.listdir(tmp_path) == ["movie.tif"]


def test_a_failed_write_leaves_the_old_file(tmp_path):
    class Broken(io.RawIOBase):
        def readinto(self, b):
            raise OSError("No space left on device")

    path = tmp_path / "movie.tif"
    path.write_bytes(OLD)
    with pytest.raises(OSError, match="No space left"):
        _write_file_atomic(str(path), Broken())
    assert path.read_bytes() == OLD
    assert os.listdir(tmp_path) == ["movie.tif"]


def test_a_long_file_name_still_works(tmp_path):
    """The temporary name does not grow with the target's (255-byte limit)."""
    path = tmp_path / ("m" * 240 + ".tif")
    _write_file_atomic(str(path), io.BytesIO(NEW))
    assert path.read_bytes() == NEW


@pytest.fixture
def input_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(files.DIRPATH, "INPUT_DIR", str(tmp_path))
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


def url_response(*chunks):
    def iter_content(chunk_size):
        for chunk in chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    return Mock(headers={"content-length": "0"}, iter_content=iter_content)


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


def test_a_url_upload_that_fails_midway_leaves_the_old_file(input_dir):
    path = input_dir / "data.csv"
    path.write_bytes(OLD)
    with patch.object(
        files.WorkspaceDataCapacityService, "is_available", return_value=False
    ):
        files.download(
            url_response(NEW[:50], ConnectionError("connection reset")), "data.csv", "1"
        )
    assert "connection reset" in files.DOWNLOAD_STATUS[str(path)].error
    assert path.read_bytes() == OLD
    assert os.listdir(input_dir) == ["data.csv"]
