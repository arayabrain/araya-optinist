import numpy as np
import pytest
import tifffile

from studio.app.optinist.dataclass.suite2p import suite2p_movie

LY, LX = 6, 5


def movie_of(frames, offset=0):
    return (np.arange(frames * LY * LX) + offset).astype(np.int16).reshape(-1, LY, LX)


def ops_for(tmp_path, frames, tiff=True):
    """Ops whose binary holds the unregistered frames and whose TIFF, when
    written, holds other, registered ones."""
    binary = tmp_path / "data.bin"
    movie_of(frames).tofile(binary)
    ops = {"Ly": LY, "Lx": LX, "nframes": frames, "reg_file": str(binary)}
    if tiff:
        path = tmp_path / "mc_images.tif"
        tifffile.imwrite(path, movie_of(frames, offset=1000))
        ops["registered_tiff"] = str(path)
    return ops


@pytest.mark.parametrize("frames", [7, 1])
def test_the_registered_tiff_is_read_memory_mapped(tmp_path, frames):
    movie = suite2p_movie(ops_for(tmp_path, frames))

    assert isinstance(movie, np.memmap)
    assert not movie.flags.writeable
    assert movie.shape == (frames, LY, LX)
    np.testing.assert_array_equal(movie, movie_of(frames, offset=1000))


def test_ops_without_a_registered_tiff_read_the_binary(tmp_path):
    # file conversion's, or a release that registered the binary in place
    movie = suite2p_movie(ops_for(tmp_path, 7, tiff=False))

    assert isinstance(movie, np.memmap)
    assert not movie.flags.writeable
    np.testing.assert_array_equal(movie, movie_of(7))


@pytest.mark.parametrize("key", ["registered_tiff", "reg_file"])
def test_a_missing_movie_is_named(tmp_path, key):
    ops = ops_for(tmp_path, 7, tiff=key == "registered_tiff")
    ops[key] = str(tmp_path / "gone")

    with pytest.raises(FileNotFoundError, match="suite2p movie not found: .*gone"):
        suite2p_movie(ops)
