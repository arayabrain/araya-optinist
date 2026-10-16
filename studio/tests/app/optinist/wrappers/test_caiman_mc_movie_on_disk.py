import contextlib
import os
import re
import sys
import types

import numpy as np
import pytest
import tifffile

from studio.app.optinist.wrappers.caiman.motion_correction import (
    C_ORDER_OFFSET,
    movie_to_tiff,
)

D1, D2, T = 9, 7, 31
FRAME_BYTES = D1 * D2 * 4


def motion_corrected(tmp_path):
    """MotionCorrect's frame-contiguous file and the C-order file save_memmap
    makes of it (each value + float32(0.0001) + float32(0)), both viewed as
    (T, d1, d2) the way caiman_mc reshapes them."""
    values = np.random.default_rng(0).normal(500, 80, (D1 * D2, T))
    values[0, :3] = [np.nan, np.inf, -0.0]
    F = np.memmap(
        tmp_path / "F.mmap", mode="w+", dtype=np.float32, shape=values.shape, order="F"
    )
    F[:] = values
    C = np.memmap(
        tmp_path / "C.mmap", mode="w+", dtype=np.float32, shape=values.shape, order="C"
    )
    C[:] = np.ascontiguousarray(F) + np.float32(0.0001) + np.float32(0.0)
    view = lambda Y: Y.T.reshape((T, D1, D2), order="F")  # noqa: E731
    return view(F), view(C)


class NoFullCopy:
    """An array-like that refuses to be converted whole and refuses reads of more
    than `limit` frames, so writing it can only go batch by batch."""

    def __init__(self, movie, limit):
        self.movie, self.limit = movie, limit
        self.shape, self.dtype = movie.shape, movie.dtype

    def __array__(self, *args, **kwargs):
        raise AssertionError("the whole movie was copied")

    def __getitem__(self, key):
        assert isinstance(key, slice), key
        start, stop, _ = key.indices(len(self.movie))
        assert stop - start <= self.limit, f"read {stop - start} frames at once"
        return self.movie[key]


@pytest.mark.parametrize("batch_bytes", [FRAME_BYTES * 4, 1])
def test_mc_images_is_byte_identical_to_writing_the_copy(tmp_path, batch_bytes):
    _, movie = motion_corrected(tmp_path)

    # 4 frames per batch does not divide the frame count; 1 byte means 1 frame
    (path,) = movie_to_tiff(
        movie, str(tmp_path / "out"), "mc_images", batch_bytes=batch_bytes
    )

    expected = tmp_path / "expected.tif"
    tifffile.imwrite(expected, np.array(movie))  # what ImageData(images) wrote
    assert open(path, "rb").read() == open(expected, "rb").read()


def test_the_frame_contiguous_file_plus_the_offset_gives_the_same_tiff(tmp_path):
    frames, movie = motion_corrected(tmp_path)

    (path,) = movie_to_tiff(
        frames,
        str(tmp_path / "out"),
        "mc_images",
        offset=C_ORDER_OFFSET,
        batch_bytes=FRAME_BYTES * 4,
    )

    expected = tmp_path / "expected.tif"
    tifffile.imwrite(expected, np.array(movie))
    assert open(path, "rb").read() == open(expected, "rb").read()


@pytest.mark.parametrize("offset", [None, C_ORDER_OFFSET])
def test_the_movie_is_written_batch_by_batch(tmp_path, offset):
    frames, _ = motion_corrected(tmp_path)

    movie_to_tiff(
        NoFullCopy(frames, limit=4),
        str(tmp_path / "out"),
        "mc_images",
        offset=offset,
        batch_bytes=FRAME_BYTES * 4,
    )


def test_the_default_batch_is_bounded_in_bytes(tmp_path):
    big = np.zeros((3, 2048, 2048), np.float32)  # 16 MiB frames
    reads = []

    class Recording(NoFullCopy):
        def __getitem__(self, key):
            reads.append(key.stop - key.start)
            return super().__getitem__(key)

    movie_to_tiff(Recording(big, limit=3), str(tmp_path / "out"), "mc_images")
    assert max(reads) * 2048 * 2048 * 4 <= 64 << 20


class Declared:
    """Declares a shape without data, for the BigTIFF decision."""

    def __init__(self, shape):
        self.shape, self.dtype = shape, np.dtype(np.float32)

    def __getitem__(self, key):
        return np.zeros((0,) + self.shape[1:], self.dtype)


@pytest.mark.parametrize("extra_frames, bigtiff", [(0, False), (1, True)])
def test_bigtiff_is_chosen_as_tifffile_chooses_it_for_the_whole_array(
    tmp_path, monkeypatch, extra_frames, bigtiff
):
    # 512 x 512 float32: tifffile's 2**32 - 2**25 byte threshold is 4,064 frames.
    # A frame iterator has no nbytes, so tifffile alone would never pick BigTIFF.
    frames = (2**32 - 2**25) // (512 * 512 * 4) + extra_frames
    seen = {}

    def imwrite(path, data, **kwargs):
        seen.update(kwargs)
        open(path, "wb").close()

    monkeypatch.setattr(tifffile, "imwrite", imwrite)
    movie_to_tiff(Declared((frames, 512, 512)), str(tmp_path), "mc_images")
    assert seen["bigtiff"] is bigtiff
    assert seen["shape"] == (frames, 512, 512)


def test_a_failed_write_leaves_no_tiff(tmp_path):
    frames, _ = motion_corrected(tmp_path)

    class FailsLater(NoFullCopy):
        def __getitem__(self, key):
            if key.start > 0:
                raise OSError("disk full")
            return super().__getitem__(key)

    out = tmp_path / "out"
    with pytest.raises(OSError, match="disk full"):
        movie_to_tiff(
            FailsLater(frames, limit=4),
            str(out),
            "mc_images",
            batch_bytes=FRAME_BYTES * 4,
        )
    assert list((out / "tiff" / "mc_images").iterdir()) == []


def test_mean_of_the_view_equals_mean_of_the_copy(tmp_path):
    _, movie = motion_corrected(tmp_path)
    # Same layout, same reduction: bit-identical, so the ROIs derived from it
    # are unchanged
    assert np.array_equal(
        movie.mean(axis=0), np.array(movie).mean(axis=0), equal_nan=True
    )


class StubCaiman:
    """Just enough of CaImAn 1.11.4 for caiman_mc. MotionCorrect writes the
    input as its frame-contiguous (F-order) file unchanged; save_memmap, which
    caiman_mc gets only when border_nan is not "copy", writes the pixels x time
    file with the border set to the movie's minimum, as border_to_0 does."""

    BORDER = 2

    def __init__(self, tmp_path, monkeypatch, border_nan):
        from studio.app.optinist.wrappers.caiman import caiman_utils, motion_correction

        self.temp = tmp_path / "caiman_temp"
        self.temp.mkdir()
        stub = self

        def fn_relocated(fn, force_temp=False):
            if os.path.split(fn)[0] == "" or force_temp:
                return os.path.join(str(stub.temp), os.path.split(fn)[1])
            return fn

        def generate_fname_tot(base_name, dims, order):
            name = f"{base_name}_d1_{dims[0]}_d2_{dims[1]}_d3_1_order_{order}"
            return fn_relocated(name, force_temp=True)

        def load_memmap(filename, mode="r"):
            d1, d2, order, T = re.search(
                r"_d1_(\d+)_d2_(\d+)_d3_1_order_(\w)_frames_(\d+)", filename
            ).groups()
            d1, d2, T = int(d1), int(d2), int(T)
            Yr = np.memmap(filename, mode=mode, dtype=np.float32, order=order)
            return Yr.reshape((d1 * d2, T), order=order), (d1, d2), T

        def save_memmap(filenames, base_name, order, border_to_0):
            Yr, dims, T = load_memmap(filenames[0])
            frames = np.array(Yr.T.reshape((T,) + dims, order="F"))
            b, low = border_to_0, frames.min()
            frames[:, :b, :] = frames[:, :, :b] = low
            frames[:, -b:, :] = frames[:, :, -b:] = low
            Yr = frames.transpose(1, 2, 0).reshape((-1, T), order="F")
            Yr = np.ascontiguousarray(Yr, dtype=np.float32) + np.float32(0.0001)
            path = generate_fname_tot(base_name, dims, order) + f"_frames_{T}.mmap"
            (Yr + np.float32(0.0)).tofile(path)
            return path

        class MotionCorrect:
            def __init__(self, fnames, dview=None, **kwargs):
                self.fnames = [fnames] if isinstance(fnames, str) else fnames
                self.border_nan, self.border_to_0 = border_nan, stub.BORDER

            def motion_correct(self, save_movie=True):
                movie = tifffile.imread(self.fnames[0]).astype(np.float32)
                T, d1, d2 = movie.shape
                path = (
                    stub.temp / f"m_rig__d1_{d1}_d2_{d2}_d3_1_order_F_frames_{T}.mmap"
                )
                F = np.memmap(
                    path, mode="w+", dtype=np.float32, shape=(d1 * d2, T), order="F"
                )
                F[:] = movie.transpose(1, 2, 0).reshape((-1, T), order="F")
                F.flush()
                self.mmap_file = [str(path)]
                self.shifts_rig = np.zeros((T, 2))

        class CNMFParams:
            def get_group(self, name):
                return {}

            def get(self, group, key):
                return False

        def masks(meanImg, gSig, expand_method):
            A = np.zeros((meanImg.size, 1))
            A[0, 0] = 1
            return (A,)

        caiman = types.ModuleType("caiman")
        caiman.__version__ = "1.11.4"
        caiman.load_memmap, caiman.save_memmap = load_memmap, save_memmap
        modules = {
            "caiman": caiman,
            "caiman.mmapping": types.SimpleNamespace(
                load_memmap=load_memmap,
                prepare_shape=lambda shape: tuple(np.uint64(n) for n in shape),
            ),
            "caiman.paths": types.SimpleNamespace(
                fn_relocated=fn_relocated, generate_fname_tot=generate_fname_tot
            ),
            "caiman.motion_correction": types.SimpleNamespace(
                MotionCorrect=MotionCorrect
            ),
            "caiman.source_extraction.cnmf.params": types.SimpleNamespace(
                CNMFParams=CNMFParams
            ),
            "caiman.base.rois": types.SimpleNamespace(
                extract_binary_masks_from_structural_channel=masks
            ),
        }
        for name, module in modules.items():
            monkeypatch.setitem(sys.modules, name, module)
        caiman.mmapping = modules["caiman.mmapping"]
        caiman.paths = modules["caiman.paths"]

        @contextlib.contextmanager
        def no_cluster(*args, **kwargs):
            yield None, 1

        monkeypatch.setattr(motion_correction, "caiman_cluster", no_cluster)
        for name in ["set_caimam_byid_tempdir", "cleanup_caiman_byid_tempdir"]:
            monkeypatch.setattr(caiman_utils.CaimanUtils, name, lambda *a: None)


@pytest.mark.parametrize("border_nan", ["copy", True])
def test_caiman_mc_writes_the_tiff_develop_main_wrote(
    tmp_path, monkeypatch, border_nan
):
    from studio.app.common.dataclass import ImageData
    from studio.app.optinist.wrappers.caiman.motion_correction import caiman_mc

    StubCaiman(tmp_path, monkeypatch, border_nan)
    movie = np.random.default_rng(3).normal(500, 80, (T, D1, D2)).astype(np.float32)
    source = str(tmp_path / "movie.tif")
    tifffile.imwrite(source, movie)
    output_dir = tmp_path / "output" / "ws" / "uid" / "caiman_mc_x"
    output_dir.mkdir(parents=True)

    info = caiman_mc(ImageData(source), str(output_dir), params=None)

    # develop-main: np.array of the pixels x time file save_memmap writes
    if border_nan == "copy":  # (x + float32(0.0001)) + float32(0)
        expected = movie + np.float32(0.0001) + np.float32(0.0)
    else:  # the border filled, which MotionCorrect's own file does not have
        b, low = StubCaiman.BORDER, movie.min()
        expected = movie.copy()
        expected[:, :b, :] = expected[:, :, :b] = low
        expected[:, -b:, :] = expected[:, :, -b:] = low
        expected = expected + np.float32(0.0001) + np.float32(0.0)
    reference = tmp_path / "expected.tif"
    tifffile.imwrite(reference, expected)
    assert open(info["mc_images"].path[0], "rb").read() == open(reference, "rb").read()
    # and its mean, over the movie in that file's layout
    C = np.ascontiguousarray(expected.transpose(1, 2, 0).reshape((-1, T), order="F"))
    develop_main = np.array(C.T.reshape((T, D1, D2), order="F"))
    np.testing.assert_array_equal(info["meanImg"].data, develop_main.mean(axis=0))
