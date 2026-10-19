import numpy as np
import pytest
import tifffile

from studio.app.optinist.wrappers.suite2p import registration
from studio.app.optinist.wrappers.suite2p.registration import binary_to_tiff

FRAMES, LY, LX = 23, 6, 5


class FakeBinaryFile:
    """What binary_to_tiff uses of suite2p.io.BinaryFile (0.10.3): int16 frames
    read from a raw file, n_frames, iter_frames(batch_size, dtype)."""

    def __init__(self, Ly, Lx, read_filename):
        self.Ly, self.Lx, self.path = Ly, Lx, read_filename

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def n_frames(self):
        return len(np.fromfile(self.path, np.int16)) // (self.Ly * self.Lx)

    def iter_frames(self, batch_size, dtype):
        movie = np.fromfile(self.path, np.int16).reshape(-1, self.Ly, self.Lx)
        for i in range(0, len(movie), batch_size):
            yield np.arange(i, min(i + batch_size, len(movie))), movie[
                i : i + batch_size
            ].astype(dtype)


class FakeIO:
    BinaryFile = FakeBinaryFile


def test_mc_images_is_byte_identical_to_writing_the_whole_movie(tmp_path):
    movie = (
        np.random.default_rng(0)
        .integers(-3000, 30000, (FRAMES, LY, LX))
        .astype(np.int16)
    )
    reg_file = tmp_path / "data.bin"
    movie.tofile(reg_file)
    ops = {"Ly": LY, "Lx": LX, "reg_file": str(reg_file)}

    # A batch size that does not divide the frame count
    (path,) = binary_to_tiff(FakeIO, ops, str(tmp_path / "out"), "mc_images", 7)

    expected = tmp_path / "expected.tif"
    tifffile.imwrite(expected, movie)  # what ImageData(mv, ...) wrote before
    assert open(path, "rb").read() == open(expected, "rb").read()
    np.testing.assert_array_equal(tifffile.imread(path), movie)


@pytest.mark.parametrize(
    "shape", [(3, LY, LX), (4, LY, LX), (FRAMES, LY, 3), (FRAMES, LY, 4)]
)
def test_shapes_tifffile_would_guess_as_rgb_are_written_grayscale(tmp_path, shape):
    movie = np.random.default_rng(0).integers(-3000, 30000, shape).astype(np.int16)
    reg_file = tmp_path / "data.bin"
    movie.tofile(reg_file)
    ops = {"Ly": shape[1], "Lx": shape[2], "reg_file": str(reg_file)}

    (path,) = binary_to_tiff(FakeIO, ops, str(tmp_path / "out"), "mc_images", 2)

    expected = tmp_path / "expected.tif"
    tifffile.imwrite(expected, movie, photometric="minisblack")
    assert open(path, "rb").read() == open(expected, "rb").read()
    with tifffile.TiffFile(path) as tif:
        assert len(tif.pages) == shape[0]
    np.testing.assert_array_equal(tifffile.imread(path), movie)


class SizedBinaryFile(FakeBinaryFile):
    """Declares a frame count without any data, for the BigTIFF decision."""

    frames = 0

    @property
    def n_frames(self):
        return self.frames

    def iter_frames(self, batch_size, dtype):
        return iter(())


@pytest.mark.parametrize("extra_frames", [0, 1])
def test_bigtiff_is_chosen_as_tifffile_chooses_it_for_the_whole_array(
    tmp_path, monkeypatch, extra_frames
):
    # 4096 x 4096 int16 frames: tifffile's threshold is then 127 frames. A frame
    # iterator has no nbytes, so tifffile alone would never pick BigTIFF.
    ly = lx = 4096
    frames = 127 + extra_frames
    # tifffile's own choice for the whole array: with no data it writes a sparse
    # file of the declared shape, deciding BigTIFF from shape and dtype
    whole = tmp_path / "whole.tif"
    tifffile.imwrite(whole, shape=(frames, ly, lx), dtype=np.int16)
    with tifffile.TiffFile(whole) as tif:
        expected = tif.is_bigtiff
    whole.unlink()
    assert expected is bool(extra_frames)

    SizedBinaryFile.frames = frames
    seen = {}

    def imwrite(path, data, **kwargs):
        seen.update(kwargs)
        open(path, "wb").close()

    monkeypatch.setattr(tifffile, "imwrite", imwrite)

    class SizedIO:
        BinaryFile = SizedBinaryFile

    ops = {"Ly": ly, "Lx": lx, "reg_file": "unused"}
    binary_to_tiff(SizedIO, ops, str(tmp_path), "mc_images")
    assert seen["bigtiff"] is expected
    assert seen["shape"] == (frames, ly, lx)


@pytest.mark.parametrize(
    "ly, lx, batch_bytes",
    [(2048, 2048, None), (6, 5, 59)],  # 59 bytes: less than one 6 x 5 frame
)
def test_default_batch_is_bounded_in_bytes(tmp_path, monkeypatch, ly, lx, batch_bytes):
    if batch_bytes is not None:
        monkeypatch.setattr(registration, "BATCH_BYTES", batch_bytes)
    batch_sizes = []

    class RecordingBinaryFile(FakeBinaryFile):
        def iter_frames(self, batch_size, dtype):
            batch_sizes.append(batch_size)
            return super().iter_frames(batch_size, dtype)

    class RecordingIO:
        BinaryFile = RecordingBinaryFile

    reg_file = tmp_path / "data.bin"
    np.zeros((9, ly, lx), np.int16).tofile(reg_file)
    ops = {"Ly": ly, "Lx": lx, "reg_file": str(reg_file)}
    binary_to_tiff(RecordingIO, ops, str(tmp_path / "out"), "mc_images")

    (batch_size,) = batch_sizes
    frame_bytes = ly * lx * 2
    # as many frames as fit, and at least one
    assert batch_size == max(1, registration.BATCH_BYTES // frame_bytes)
    assert batch_size == 1 or batch_size * frame_bytes <= registration.BATCH_BYTES


@pytest.mark.parametrize("error", [OSError("disk full"), KeyboardInterrupt()])
def test_a_failed_write_leaves_the_previous_tiff_untouched(tmp_path, error):
    class FailingBinaryFile(FakeBinaryFile):
        def iter_frames(self, batch_size, dtype):
            yield from list(super().iter_frames(batch_size, dtype))[:1]
            raise error

    class FailingIO:
        BinaryFile = FailingBinaryFile

    reg_file = tmp_path / "data.bin"
    np.zeros((FRAMES, LY, LX), np.int16).tofile(reg_file)
    ops = {"Ly": LY, "Lx": LX, "reg_file": str(reg_file)}
    tiff_dir = tmp_path / "out" / "tiff" / "mc_images"
    tiff_dir.mkdir(parents=True)
    # mc_images.tif from an earlier run of the node
    previous = tiff_dir / "mc_images.tif"
    previous.write_bytes(b"earlier run")
    with pytest.raises(type(error)):
        binary_to_tiff(FailingIO, ops, str(tmp_path / "out"), "mc_images", 7)
    # no partial file, under the final name or beside it
    assert list(tiff_dir.iterdir()) == [previous]
    assert previous.read_bytes() == b"earlier run"
