import os
import sys
import types

import numpy as np
import pytest
import tifffile

from studio.app.optinist.wrappers.suite2p import file_convert
from studio.app.optinist.wrappers.suite2p.file_convert import tiff_list_for_binary

FRAMES, LY, LX = 23, 6, 5
PARAMS = {"force_sktiff": False, "batch_size": 7}


class FakeScanImageTiff:
    """What file_convert uses of ScanImageTiffReader: data() and data(beg, end)."""

    def __init__(self, path):
        self.path = path

    def data(self, beg=None, end=None):
        im = tifffile.imread(self.path)
        return im if beg is None else im[beg:end]


def open_tiff(path, sktiff):
    assert not sktiff, "file_convert reads tifffile files with tifffile itself"
    return FakeScanImageTiff(path), None


def readers(needs_tifffile=()):
    """use_sktiff_reader that answers True for the named files, recording calls."""
    calls = []

    def use_sktiff_reader(path, batch_size=None):
        calls.append(path)
        return os.path.basename(path) in needs_tifffile

    return use_sktiff_reader, calls


def whole_file_copy(movie):
    """develop-main's copy of one input, as bytes."""
    if movie.dtype.type == np.float32:
        movie = (movie - movie.min()) / (movie.max() - movie.min())
        movie = (movie * np.iinfo(np.int16).max).astype(np.int16)
    return movie


def write(path, movie, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    tifffile.imwrite(path, movie, **kwargs)
    return str(path)


def out_dir(tmp_path):
    """The node directory, which exists before the function runs."""
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return str(out)


def expected_bytes(tmp_path, movie):
    expected = tmp_path / "expected.tif"
    tifffile.imwrite(expected, whole_file_copy(movie))
    return expected.read_bytes()


def movie_of(dtype, shape=(FRAMES, LY, LX)):
    rng = np.random.default_rng(0)
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        return rng.integers(info.min, info.max, shape, endpoint=True).astype(dtype)
    return (rng.standard_normal(shape) * 50 - 7).astype(dtype)


@pytest.mark.parametrize("dtype", [np.uint16, np.int16, np.int32, np.uint8])
@pytest.mark.parametrize("shape", [(FRAMES, LY, LX), (LY, LX)])
def test_integer_input_is_listed_by_its_own_path_and_not_copied(tmp_path, dtype, shape):
    path = write(tmp_path / "in" / "movie.tif", movie_of(dtype, shape))
    out = tmp_path / "out"
    out.mkdir()
    use_sktiff_reader, _ = readers()

    tiff_list = tiff_list_for_binary(
        [path], str(out), PARAMS, open_tiff, use_sktiff_reader
    )

    assert tiff_list == [os.path.abspath(path)]
    # tiff_to_binary joins it to data_path[0], which an absolute path replaces
    assert os.path.join(str(out), tiff_list[0]) == os.path.abspath(path)
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("needs_tifffile", [(), ("movie.tif",)])
@pytest.mark.parametrize("batch_bytes", [None, 2 * LY * LX * 4 + 1])
def test_float32_copy_is_byte_identical_to_the_whole_file_copy(
    tmp_path, monkeypatch, needs_tifffile, batch_bytes
):
    if batch_bytes is not None:
        monkeypatch.setattr(file_convert, "BATCH_BYTES", batch_bytes)
    movie = movie_of(np.float32)
    movie[5, 2, 3] = 1e6  # the max sits in a later batch
    path = write(tmp_path / "in" / "movie.tif", movie)
    use_sktiff_reader, _ = readers(needs_tifffile)

    tiff_list = tiff_list_for_binary(
        [path], out_dir(tmp_path), PARAMS, open_tiff, use_sktiff_reader
    )

    assert tiff_list == ["movie.tif"]
    copy = tmp_path / "out" / "movie.tif"
    assert copy.read_bytes() == expected_bytes(tmp_path, movie)


@pytest.mark.parametrize("case", ["nan", "constant"])
def test_float32_copy_keeps_the_whole_array_result_for_nan_and_constant_input(
    tmp_path, monkeypatch, case
):
    monkeypatch.setattr(file_convert, "BATCH_BYTES", 3 * LY * LX * 4)
    movie = movie_of(np.float32)
    if case == "nan":
        movie[17, 3, 4] = np.nan
    else:
        movie[:] = 2.5
    path = write(tmp_path / "in" / "movie.tif", movie)
    use_sktiff_reader, _ = readers()

    with np.errstate(invalid="ignore"):
        tiff_list_for_binary(
            [path], out_dir(tmp_path), PARAMS, open_tiff, use_sktiff_reader
        )
        expected = expected_bytes(tmp_path, movie)

    assert (tmp_path / "out" / "movie.tif").read_bytes() == expected


@pytest.mark.parametrize(
    "name, movie, kwargs",
    [
        ("float64", movie_of(np.float64), {}),
        ("float32 3 frames", movie_of(np.float32, (3, LY, LX)), {}),
        ("float32 3 columns", movie_of(np.float32, (FRAMES, LY, 3)), {}),
        ("float32 one frame", movie_of(np.float32, (LY, LX)), {}),
        ("uint16 3 frames", movie_of(np.uint16, (3, LY, LX)), {}),
        ("imagej hyperstack", movie_of(np.uint16, (FRAMES, 2, LY, LX)), {}),
    ],
)
def test_other_inputs_get_the_whole_file_copy(tmp_path, name, movie, kwargs):
    if name == "imagej hyperstack":
        kwargs = {"imagej": True}
    path = write(tmp_path / "in" / "movie.tif", movie, **kwargs)
    use_sktiff_reader, _ = readers(("movie.tif",))

    tiff_list = tiff_list_for_binary(
        [path], out_dir(tmp_path), PARAMS, open_tiff, use_sktiff_reader
    )

    assert tiff_list == ["movie.tif"]
    expected = tmp_path / "expected.tif"
    tifffile.imwrite(expected, whole_file_copy(tifffile.imread(path)))
    assert (tmp_path / "out" / "movie.tif").read_bytes() == expected.read_bytes()


def test_a_file_with_two_series_gets_the_whole_file_copy(tmp_path):
    path = tmp_path / "in" / "movie.tif"
    path.parent.mkdir()
    with tifffile.TiffWriter(path) as tif:
        tif.write(movie_of(np.uint16))
        tif.write(movie_of(np.uint16, (5, LY - 1, LX)))
    use_sktiff_reader, _ = readers(("movie.tif",))

    tiff_list = tiff_list_for_binary(
        [str(path)], out_dir(tmp_path), PARAMS, open_tiff, use_sktiff_reader
    )

    assert tiff_list == ["movie.tif"]


def test_an_unreadable_file_gets_the_whole_file_copy_and_its_error(tmp_path):
    path = tmp_path / "in" / "movie.tif"
    path.parent.mkdir()
    path.write_bytes(b"not a tiff")
    use_sktiff_reader, _ = readers(("movie.tif",))

    with pytest.raises(Exception):
        tiff_list_for_binary(
            [str(path)], out_dir(tmp_path), PARAMS, open_tiff, use_sktiff_reader
        )


@pytest.mark.parametrize(
    "needs_tifffile, force, expected",
    [
        # tiff_to_binary reads every file with the reader of the first
        ((), False, ["as is", "as is"]),
        (("b.tif",), False, ["as is", "copy"]),
        (("a.tif",), False, ["as is", "copy"]),
        (("a.tif", "b.tif"), False, ["as is", "as is"]),
        (("b.tif",), True, ["as is", "as is"]),
    ],
)
def test_a_file_whose_reader_differs_from_the_first_files_is_copied(
    tmp_path, needs_tifffile, force, expected
):
    paths = [
        write(tmp_path / "in1" / "a.tif", movie_of(np.uint16)),
        write(tmp_path / "in2" / "b.tif", movie_of(np.uint16)),
    ]
    use_sktiff_reader, _ = readers(needs_tifffile)
    params = {**PARAMS, "force_sktiff": force}

    tiff_list = tiff_list_for_binary(
        paths, out_dir(tmp_path), params, open_tiff, use_sktiff_reader
    )

    assert ["as is" if os.path.isabs(n) else "copy" for n in tiff_list] == expected


@pytest.mark.parametrize("copy_reader_needs_tifffile", [False, True])
def test_when_the_first_file_is_copied_its_copy_sets_the_reader(
    tmp_path, copy_reader_needs_tifffile
):
    paths = [
        write(tmp_path / "in" / "a.tif", movie_of(np.float32)),
        write(tmp_path / "in" / "b.tif", movie_of(np.uint16)),
    ]
    out = out_dir(tmp_path)
    copy = os.path.join(out, "a.tif")
    calls = []

    def use_sktiff_reader(path, batch_size=None):
        calls.append(path)
        return copy_reader_needs_tifffile and path == copy

    tiff_list = tiff_list_for_binary(paths, out, PARAMS, open_tiff, use_sktiff_reader)

    assert copy in calls
    # b.tif reads with ScanImageTiffReader: as is only if the copy does too
    assert os.path.isabs(tiff_list[1]) is not copy_reader_needs_tifffile


@pytest.mark.parametrize("extra_frames", [0, 1])
def test_bigtiff_is_chosen_as_tifffile_chooses_it_for_the_whole_array(
    tmp_path, monkeypatch, extra_frames
):
    # 4096 x 4096 int16 frames: tifffile's threshold is then 127 frames
    shape = (127 + extra_frames, 4096, 4096)
    whole = tmp_path / "whole.tif"
    tifffile.imwrite(whole, shape=shape, dtype=np.int16)
    with tifffile.TiffFile(whole) as tif:
        expected = tif.is_bigtiff
    whole.unlink()
    assert expected is bool(extra_frames)

    seen = {}

    def imwrite(path, data, **kwargs):
        seen.update(kwargs)
        open(path, "wb").close()

    monkeypatch.setattr(file_convert, "iter_batches", lambda *args: iter(()))
    monkeypatch.setattr(tifffile, "imwrite", imwrite)
    file_convert.write_normalised_copy(
        "unused", str(tmp_path / "copy.tif"), shape, True, open_tiff
    )
    assert seen["bigtiff"] is expected
    assert seen["shape"] == shape


@pytest.mark.parametrize("error", [OSError("disk full"), KeyboardInterrupt()])
def test_a_failed_copy_leaves_no_partial_file(tmp_path, monkeypatch, error):
    monkeypatch.setattr(file_convert, "BATCH_BYTES", 2 * LY * LX * 4)
    path = write(tmp_path / "in" / "movie.tif", movie_of(np.float32))
    out = tmp_path / "out"
    out.mkdir()
    real_iter_batches = file_convert.iter_batches
    passes = []

    def failing_iter_batches(*args):
        passes.append(1)
        batches = real_iter_batches(*args)
        if len(passes) == 2:  # the writing pass
            yield next(batches)
            raise error
        yield from batches

    monkeypatch.setattr(file_convert, "iter_batches", failing_iter_batches)
    use_sktiff_reader, _ = readers()
    with pytest.raises(type(error)):
        tiff_list_for_binary([path], str(out), PARAMS, open_tiff, use_sktiff_reader)
    assert list(out.iterdir()) == []


def test_suite2p_file_convert_hands_tiff_to_binary_the_list(tmp_path, monkeypatch):
    paths = [
        write(tmp_path / "in" / "a.tif", movie_of(np.uint16)),
        write(tmp_path / "in" / "b.tif", movie_of(np.float32)),
    ]
    out = tmp_path / "out"
    out.mkdir()
    seen = {}

    def tiff_to_binary(ops):
        seen.update(ops)
        return {**ops, "meanImg": np.zeros((LY, LX), np.float32)}

    use_sktiff_reader, _ = readers()
    tiff = types.SimpleNamespace(
        open_tiff=open_tiff, use_sktiff_reader=use_sktiff_reader
    )
    io = types.SimpleNamespace(tiff_to_binary=tiff_to_binary, tiff=tiff)
    suite2p = types.SimpleNamespace(default_ops=lambda: {"nplanes": 1}, io=io)
    monkeypatch.setitem(sys.modules, "suite2p", suite2p)
    monkeypatch.setitem(sys.modules, "suite2p.io", io)
    monkeypatch.setitem(sys.modules, "suite2p.io.tiff", tiff)

    image = types.SimpleNamespace(path=paths)
    file_convert.suite2p_file_convert(image, str(out), params=dict(PARAMS))

    assert seen["tiff_list"] == [os.path.abspath(paths[0]), "b.tif"]
    # data_path[0] stays the node directory: copies resolve there, and
    # registration looks for bad_frames.npy there
    assert seen["data_path"] == [str(out)] * 2
    assert seen["save_path0"] == str(out)
