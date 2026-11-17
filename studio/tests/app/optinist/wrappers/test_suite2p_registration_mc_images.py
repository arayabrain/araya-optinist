import errno
import os

import numpy as np
import pytest
import tifffile

from studio.app.optinist.wrappers.suite2p import utils
from studio.app.optinist.wrappers.suite2p.utils import (
    FrameCopies,
    tiff_path,
    writable_tiff,
)

FRAMES, LY, LX = 23, 6, 5


def movie_of(shape):
    return np.random.default_rng(0).integers(-3000, 30000, shape).astype(np.int16)


@pytest.mark.parametrize(
    "shape",
    # the last three are shapes tifffile stores as RGB
    [(FRAMES, LY, LX), (1, LY, LX), (3, LY, LX), (4, LY, LX), (FRAMES, LY, 3)],
)
def test_a_filled_tiff_is_byte_identical_to_writing_the_whole_movie(tmp_path, shape):
    movie = movie_of(shape)

    with writable_tiff(str(tmp_path / "out"), "mc_images", shape) as frames:
        assert frames.shape == shape
        for t in range(shape[0]):  # filled frame by frame, as suite2p writes
            frames[t : t + 1] = movie[t : t + 1]

    path = tiff_path(str(tmp_path / "out"), "mc_images")
    assert path == f"{tmp_path}/out/tiff/mc_images/mc_images.tif"
    expected = tmp_path / "expected.tif"
    tifffile.imwrite(expected, movie)  # what ImageData(movie, ...) writes
    assert open(path, "rb").read() == open(expected, "rb").read()


@pytest.mark.parametrize("extra_frames", [0, 1])
def test_bigtiff_is_chosen_as_tifffile_chooses_it_for_the_whole_array(
    tmp_path, monkeypatch, extra_frames
):
    # 4096 x 4096 int16 frames: tifffile's threshold is then 127 frames. Both
    # files stay sparse: no frame is written and no block reserved.
    monkeypatch.setattr(utils, "reserve_blocks", lambda path: None)
    shape = (127 + extra_frames, 4096, 4096)
    whole = tmp_path / "whole.tif"
    tifffile.imwrite(whole, shape=shape, dtype=np.int16)
    with tifffile.TiffFile(whole) as tif:
        expected = tif.is_bigtiff
    whole.unlink()
    assert expected is bool(extra_frames)

    with writable_tiff(str(tmp_path), "mc_images", shape):
        pass

    with tifffile.TiffFile(tiff_path(str(tmp_path), "mc_images")) as tif:
        assert tif.is_bigtiff is expected


@pytest.mark.parametrize("error", [OSError("disk full"), KeyboardInterrupt()])
def test_a_failed_fill_leaves_the_previous_tiff_untouched(tmp_path, error):
    tiff_dir = tmp_path / "out" / "tiff" / "mc_images"
    tiff_dir.mkdir(parents=True)
    # mc_images.tif from an earlier run of the node
    previous = tiff_dir / "mc_images.tif"
    previous.write_bytes(b"earlier run")

    with pytest.raises(type(error)):
        with writable_tiff(str(tmp_path / "out"), "mc_images", (FRAMES, LY, LX)):
            raise error

    # no partial file, under the final name or beside it
    assert list(tiff_dir.iterdir()) == [previous]
    assert previous.read_bytes() == b"earlier run"


def test_a_partial_file_that_cannot_be_removed_does_not_hide_the_error(
    tmp_path, monkeypatch
):
    # Windows refuses to remove a file the failing caller still maps
    def remove(path):
        raise PermissionError(errno.EACCES, "in use", path)

    monkeypatch.setattr(os, "remove", remove)

    with pytest.raises(RuntimeError, match="registration failed"):
        with writable_tiff(str(tmp_path), "mc_images", (FRAMES, LY, LX)):
            raise RuntimeError("registration failed")


def test_the_whole_file_is_reserved_before_it_is_mapped(tmp_path, monkeypatch):
    events = []
    memmap = tifffile.memmap

    def posix_fallocate(fd, offset, length):
        events.append(("reserve", offset, length, os.fstat(fd).st_size))

    def recording_memmap(*args, **kwargs):
        events.append(("map",))
        return memmap(*args, **kwargs)

    # not on macOS: added there for the test
    monkeypatch.setattr(os, "posix_fallocate", posix_fallocate, raising=False)
    monkeypatch.setattr(tifffile, "memmap", recording_memmap)

    with writable_tiff(str(tmp_path), "mc_images", (FRAMES, LY, LX)):
        pass

    size = os.path.getsize(tiff_path(str(tmp_path), "mc_images"))
    assert events == [("reserve", 0, size, size), ("map",)]


def test_a_full_disk_is_an_error_and_leaves_no_partial_file(tmp_path, monkeypatch):
    def posix_fallocate(fd, offset, length):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "posix_fallocate", posix_fallocate, raising=False)
    tiff_dir = tmp_path / "out" / "tiff" / "mc_images"
    tiff_dir.mkdir(parents=True)
    previous = tiff_dir / "mc_images.tif"
    previous.write_bytes(b"earlier run")

    with pytest.raises(OSError, match="No space left"):
        with writable_tiff(str(tmp_path / "out"), "mc_images", (FRAMES, LY, LX)):
            pytest.fail("the movie was handed out unreserved")

    assert list(tiff_dir.iterdir()) == [previous]
    assert previous.read_bytes() == b"earlier run"


def test_frame_copies_can_be_written_and_leave_the_movie_alone(tmp_path):
    movie = movie_of((FRAMES, LY, LX))
    path = tmp_path / "data.bin"
    movie.tofile(path)
    binary = np.memmap(path, np.int16, "r", shape=movie.shape)
    assert not binary[0:2].flags.writeable

    frames = FrameCopies(binary)
    assert frames.shape == movie.shape
    batch = frames[2:9]
    # suite2p's in-place bidiphase shift on what it read
    batch[:, 1::2, 1:] = batch[:, 1::2, :-1]
    np.testing.assert_array_equal(frames[[0, 5]], movie[[0, 5]])

    del binary
    np.testing.assert_array_equal(np.fromfile(path, np.int16), movie.ravel())
