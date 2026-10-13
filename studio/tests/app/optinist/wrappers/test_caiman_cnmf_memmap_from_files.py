import os

import numpy as np
import pytest
import tifffile

from studio.app.common.dataclass import ImageData

pytest.importorskip("caiman")

from studio.app.optinist.wrappers.caiman.cnmf import (  # noqa: E402
    util_get_image_memmap,
    util_get_image_memmap_from_files,
)


def write(path, frames, dtype):
    rng = np.random.default_rng(len(str(path)))
    movie = rng.normal(300, 60, (frames, 9, 7)).clip(0).astype(dtype)
    tifffile.imwrite(path, movie)
    return str(path)


@pytest.mark.parametrize(
    "files",
    [
        [("mc_images.tif", 23, np.float32)],  # caiman_mc's output
        [("a.tif", 11, np.uint16), ("b.tif", 6, np.uint16)],  # a list of inputs
    ],
)
def test_same_memmap_as_reading_the_whole_movie(tmp_path, files):
    paths = [write(tmp_path / name, n, dt) for name, n, dt in files]
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    expected, dims_e, path_e = util_get_image_memmap(
        "fid", ImageData(paths).data, str(old_dir / os.path.basename(paths[0]))
    )
    # A batch size that does not divide the frame counts
    actual, dims_a, path_a = util_get_image_memmap_from_files(
        "fid", paths, str(new_dir / os.path.basename(paths[0])), batch_size=4
    )

    assert tuple(dims_a) == tuple(dims_e)
    assert os.path.basename(path_a) == os.path.basename(path_e)
    assert actual.shape == expected.shape
    del actual, expected
    assert open(path_a, "rb").read() == open(path_e, "rb").read()
