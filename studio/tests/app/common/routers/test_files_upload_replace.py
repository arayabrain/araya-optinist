"""An upload replaces its file through a temporary file beside it, so a reader
that has the old file open (a running workflow) keeps reading the old file, and
a failed upload leaves the old file in place."""
import io
import os

import pytest

from studio.app.common.routers.files import _write_file_atomic

OLD, NEW = b"old movie " * 1000, b"new movie " * 10


def test_a_reader_of_the_old_file_keeps_reading_it(tmp_path):
    path = tmp_path / "movie.tif"
    path.write_bytes(OLD)
    with open(path, "rb") as reader:
        head = reader.read(10)
        _write_file_atomic(str(path), io.BytesIO(NEW))
        assert head + reader.read() == OLD
    assert path.read_bytes() == NEW
    assert os.listdir(tmp_path) == ["movie.tif"]


def test_a_failed_write_leaves_the_old_file(tmp_path):
    class Broken(io.RawIOBase):
        def readinto(self, b):
            raise OSError("connection reset")

    path = tmp_path / "movie.tif"
    path.write_bytes(OLD)
    with pytest.raises(OSError, match="connection reset"):
        _write_file_atomic(str(path), Broken())
    assert path.read_bytes() == OLD
    assert os.listdir(tmp_path) == ["movie.tif"]
