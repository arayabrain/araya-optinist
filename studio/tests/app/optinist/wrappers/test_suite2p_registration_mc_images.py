import numpy as np
import tifffile

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
