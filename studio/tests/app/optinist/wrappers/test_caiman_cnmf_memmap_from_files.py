import os
import sys
import types
import zlib

import numpy as np
import pytest
import tifffile

from studio.app.common.dataclass import ImageData
from studio.app.optinist.wrappers.caiman.cnmf import (
    util_get_image_memmap,
    util_get_image_memmap_from_files,
)


@pytest.fixture(autouse=True)
def caiman_names(monkeypatch):
    """Both functions take the memmap's shape and name from caiman. Without
    caiman (the app image), stand-ins for the two helpers let this run: both
    functions use them, so the byte comparison still holds."""
    try:
        import caiman  # noqa: F401
    except ImportError:
        mmapping = types.ModuleType("caiman.mmapping")
        mmapping.prepare_shape = lambda shape: tuple(np.uint64(n) for n in shape)
        paths = types.ModuleType("caiman.paths")
        paths.memmap_frames_filename = lambda base, dims, T, order: (
            f"{base}_d1_{dims[0]}_d2_{dims[1]}_d3_{dims[2] if len(dims) == 3 else 1}"
            f"_order_{order}_frames_{T}.mmap"
        )
        root = types.ModuleType("caiman")
        root.mmapping, root.paths = mmapping, paths
        for name, module in [
            ("caiman", root),
            ("caiman.mmapping", mmapping),
            ("caiman.paths", paths),
        ]:
            monkeypatch.setitem(sys.modules, name, module)


def write(path, frames, dtype, frame_shape=(9, 7), **kwargs):
    rng = np.random.default_rng(zlib.crc32(os.path.basename(str(path)).encode()))
    movie = rng.normal(300, 60, (frames, *frame_shape)).clip(0).astype(dtype)
    tifffile.imwrite(path, movie, **kwargs)
    return str(path)


ZLIB = {"compression": "zlib"}
VOLUME = {"frame_shape": (3, 9, 7), "compression": "zlib"}  # pages are planes


@pytest.mark.parametrize(
    "files",
    [
        [("mc_images.tif", 23, np.float32, {})],  # caiman_mc's output
        [("a.tif", 11, np.uint16, {}), ("b.tif", 6, np.uint16, {})],  # inputs
        [("c.tif", 5, np.uint8, {}), ("d.tif", 4, np.float32, {})],  # mixed dtypes
        [("zlib.tif", 9, np.uint16, ZLIB)],  # decoded a batch of pages at a time
        [("z.tif", 6, np.uint16, ZLIB), ("raw.tif", 3, np.uint16, {})],
        [("volume.tif", 5, np.uint16, VOLUME)],  # read in full
    ],
)
def test_same_memmap_as_reading_the_whole_movie(tmp_path, files):
    paths = [write(tmp_path / name, n, dt, **kw) for name, n, dt, kw in files]
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


@pytest.mark.parametrize(
    "second",
    [
        lambda path: write(path, 4, np.uint16, frame_shape=(1, 7)),  # (T, 1, X)
        lambda path: write(path, 4, np.uint16, frame_shape=(9, 1)),  # (T, Y, 1)
        lambda path: tifffile.imwrite(path, np.zeros((9, 7), np.uint16)) or path,
    ],
    ids=["one-row frames", "one-column frames", "a single 2D frame"],
)
@pytest.mark.parametrize("batch_size", [4, 1024])
def test_files_that_do_not_stack_are_rejected(tmp_path, second, batch_size):
    # Concatenating these raises; assigning them per file would broadcast
    first = write(tmp_path / "first.tif", 5, np.uint16)
    other = str(second(str(tmp_path / "other.tif")))
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError, match="other.tif"):
        util_get_image_memmap_from_files(
            "fid", [first, other], str(out / "first.tif"), batch_size=batch_size
        )
    with pytest.raises(ValueError):
        ImageData([first, other]).data  # as develop-main read them
    assert list(out.iterdir()) == []  # no memmap allocated


def test_a_single_2d_file_is_rejected(tmp_path):
    path = str(tmp_path / "frame.tif")
    tifffile.imwrite(path, np.zeros((9, 7), np.uint16))
    with pytest.raises(ValueError, match="not a stack"):
        util_get_image_memmap_from_files("fid", [path], path)


def test_a_compressed_stack_is_never_read_whole(tmp_path, monkeypatch):
    import imageio

    path = write(tmp_path / "zlib.tif", 9, np.uint16, compression="zlib")
    monkeypatch.setattr(imageio, "volread", lambda *a, **k: pytest.fail("read whole"))
    out = tmp_path / "out"
    out.mkdir()
    images, _, _ = util_get_image_memmap_from_files(
        "fid", [path], str(out / "zlib.tif"), batch_size=4
    )
    np.testing.assert_array_equal(images, tifffile.imread(path))


def test_a_failed_fill_leaves_no_memmap_and_keeps_its_error(tmp_path, monkeypatch):
    path = write(tmp_path / "a.tif", 9, np.uint16)
    mapped = tifffile.memmap

    class FailsAfterOneBatch:
        def __init__(self, source):
            self.source, self.reads = source, 0

        def __len__(self):
            return len(self.source)

        def __getitem__(self, key):
            self.reads += 1
            if self.reads > 1:
                raise OSError("read error")
            return self.source[key]

    monkeypatch.setattr(
        tifffile, "memmap", lambda *a, **k: FailsAfterOneBatch(mapped(*a, **k))
    )
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(OSError, match="read error"):
        util_get_image_memmap_from_files("fid", [path], str(out / "a.tif"), 4)
    assert list(out.iterdir()) == []


def test_caiman_cnmf_fills_the_memmap_from_every_file(tmp_path, monkeypatch):
    from studio.app.optinist.wrappers.caiman import cnmf

    for name in [
        "caiman",
        "caiman.source_extraction",
        "caiman.source_extraction.cnmf",
        "caiman.source_extraction.cnmf.params",
    ]:
        module = types.ModuleType(name)
        module.local_correlations = module.cnmf = module.online_cnmf = None
        module.CNMFParams = None
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(cnmf, "util_download_model_files", lambda: None)

    class Called(Exception):
        pass

    def record(function_id, paths, file_path, *args, **kwargs):
        raise Called(paths)

    monkeypatch.setattr(cnmf, "util_get_image_memmap_from_files", record)
    paths = [write(tmp_path / n, 3, np.uint16) for n in ("s1.tif", "s2.tif")]
    output_dir = tmp_path / "output" / "default" / "uid" / "caiman_cnmf_x"
    output_dir.mkdir(parents=True)
    with pytest.raises(Called) as called:
        cnmf.caiman_cnmf(ImageData(paths), str(output_dir), params={})
    assert called.value.args[0] == paths
