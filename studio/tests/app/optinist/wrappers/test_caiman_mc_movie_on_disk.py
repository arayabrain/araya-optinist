import numpy as np
import tifffile

from studio.app.optinist.wrappers.caiman.motion_correction import movie_to_tiff

D1, D2, T = 9, 7, 31


def corrected_movie(tmp_path):
    """What caiman_mc reads back: a pixels x time C-order memmap, viewed as
    (T, d1, d2) the way the wrapper reshapes it."""
    path = tmp_path / "Yr.mmap"
    values = np.random.default_rng(0).normal(500, 80, (D1 * D2, T))
    Yr = np.memmap(path, mode="w+", dtype=np.float32, shape=values.shape, order="C")
    Yr[:] = values
    return Yr, Yr.T.reshape((T, D1, D2), order="F")


def test_mc_images_is_byte_identical_to_writing_the_copy(tmp_path):
    _, movie = corrected_movie(tmp_path)

    # A batch size that does not divide the frame count
    (path,) = movie_to_tiff(movie, str(tmp_path / "out"), "mc_images", batch_size=4)

    expected = tmp_path / "expected.tif"
    tifffile.imwrite(expected, np.array(movie))  # what ImageData(images) wrote
    assert open(path, "rb").read() == open(expected, "rb").read()


def test_mean_of_the_view_equals_mean_of_the_copy(tmp_path):
    _, movie = corrected_movie(tmp_path)
    # Same layout, same reduction: bit-identical, so the ROIs derived from it
    # are unchanged
    assert np.array_equal(movie.mean(axis=0), np.array(movie).mean(axis=0))
