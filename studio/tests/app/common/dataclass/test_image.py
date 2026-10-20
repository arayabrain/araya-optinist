import os
import struct

import imageio
import numpy as np
import pytest
import tifffile

from studio.app.common.dataclass import image as image_module
from studio.app.common.dataclass.image import ImageData


def make_image_data(tmp_path, frames=10):
    data = np.arange(frames * 4 * 4, dtype=np.uint16).reshape(frames, 4, 4)
    return ImageData(data, output_dir=str(tmp_path), file_name="test_image"), data


def test_split_image_equal_parts(tmp_path):
    image_data, data = make_image_data(tmp_path, frames=10)
    paths = image_data.split_image(str(tmp_path), n_files=3)
    parts = [tifffile.imread(p) for p in paths]
    assert [p.shape[0] for p in parts] == [4, 3, 3]
    np.testing.assert_array_equal(np.concatenate(parts), data)


def test_split_image_explicit_lengths(tmp_path):
    image_data, data = make_image_data(tmp_path, frames=10)
    paths = image_data.split_image(str(tmp_path), lengths=[2, 8])
    parts = [tifffile.imread(p) for p in paths]
    assert [p.shape[0] for p in parts] == [2, 8]
    np.testing.assert_array_equal(np.concatenate(parts), data)


def test_split_image_lengths_sum_mismatch_raises(tmp_path):
    image_data, _ = make_image_data(tmp_path, frames=10)
    with pytest.raises(ValueError, match="sum of lengths"):
        image_data.split_image(str(tmp_path), lengths=[2, 4])


def test_split_image_lengths_non_positive_raises(tmp_path):
    image_data, _ = make_image_data(tmp_path, frames=10)
    with pytest.raises(ValueError, match="positive"):
        image_data.split_image(str(tmp_path), lengths=[0, 10])


def test_split_image_lengths_non_integer_raises(tmp_path):
    image_data, _ = make_image_data(tmp_path, frames=10)
    with pytest.raises(ValueError, match="integers"):
        image_data.split_image(str(tmp_path), lengths=["abc", 10])


def test_split_image_lengths_non_integral_raises(tmp_path):
    image_data, _ = make_image_data(tmp_path, frames=10)
    with pytest.raises(ValueError, match="integers"):
        image_data.split_image(str(tmp_path), lengths=[2.9, 7.1])


def test_split_image_single_length_raises(tmp_path):
    image_data, _ = make_image_data(tmp_path, frames=10)
    with pytest.raises(ValueError, match="at least 2"):
        image_data.split_image(str(tmp_path), lengths=[10])


def test_split_image_n_files_non_integral_raises(tmp_path):
    image_data, _ = make_image_data(tmp_path, frames=10)
    with pytest.raises(ValueError, match="n_files must be an integer"):
        image_data.split_image(str(tmp_path), n_files=2.5)


def test_split_image_n_files_too_small_raises(tmp_path):
    image_data, _ = make_image_data(tmp_path, frames=10)
    with pytest.raises(ValueError, match="greater than 1"):
        image_data.split_image(str(tmp_path), n_files=1)


def test_split_image_more_parts_than_frames_raises(tmp_path):
    image_data, _ = make_image_data(tmp_path, frames=2)
    with pytest.raises(ValueError, match="cannot split"):
        image_data.split_image(str(tmp_path), n_files=3)


def test_shape_comes_from_the_headers_and_matches_data(tmp_path):
    image_data, data = make_image_data(tmp_path, frames=10)
    assert image_data.shape == image_data.data.shape == data.shape
    assert image_data.ndim == 3

    # Several files stack along frames, as data's concatenate does
    second = tmp_path / "second.tif"
    tifffile.imwrite(second, data[:3])
    both = ImageData([image_data.path[0], str(second)])
    assert both.shape == both.data.shape == (13, 4, 4)


def test_shape_of_a_single_frame_is_2d(tmp_path):
    frame = np.ones((4, 5), dtype=np.uint16)
    image_data = ImageData(frame, output_dir=str(tmp_path), file_name="frame")
    assert image_data.shape == image_data.data.shape == (4, 5)
    assert image_data.ndim == 2


def test_shape_falls_back_to_reading_when_files_do_not_stack(tmp_path):
    a, b = tmp_path / "a.tif", tmp_path / "b.tif"
    tifffile.imwrite(a, np.zeros((2, 4, 4), np.uint16))
    tifffile.imwrite(b, np.zeros((2, 4, 6), np.uint16))
    image_data = ImageData([str(a), str(b)])
    with pytest.raises(ValueError):
        # data cannot concatenate these either; the fallback surfaces that
        image_data.shape


def test_output_path_and_save_json_do_not_read_a_stack(tmp_path, monkeypatch):
    image_data, _ = make_image_data(tmp_path, frames=10)

    def no_full_read(self):
        raise AssertionError("the whole stack was read")

    monkeypatch.setattr(ImageData, "data", property(no_full_read))
    image_data.save_json(str(tmp_path))  # a 3D stack writes no JSON
    assert image_data.output_path.max_index == 10
    assert image_data.output_path.path == image_data.path[0]

    # nor does a list of files, the image input node's form
    second = tmp_path / "second.tif"
    tifffile.imwrite(second, np.zeros((3, 4, 4), np.uint16))
    both = ImageData([image_data.path[0], str(second)])
    both.save_json(str(tmp_path))
    assert both.output_path.max_index == 10


def _imagej_tzcyx(path):
    tifffile.imwrite(
        path,
        np.zeros((3, 2, 2, 5, 6), np.uint16),
        imagej=True,
        metadata={"axes": "TZCYX"},
    )


def _ome_two_series(path):
    with tifffile.TiffWriter(path, ome=True) as tif:
        tif.write(np.zeros((4, 5, 6), np.uint16), metadata={"axes": "TYX"})
        tif.write(np.zeros((2, 7, 8), np.uint16), metadata={"axes": "TYX"})


def _single_frame_stack(path):
    tifffile.imwrite(path, np.zeros((1, 5, 6), np.uint16))


def _pages_without_shape_metadata(path):
    with tifffile.TiffWriter(path) as tif:
        for _ in range(4):
            tif.write(np.zeros((5, 6), np.uint16), metadata=None)


def _rgb(path):
    tifffile.imwrite(path, np.zeros((5, 6, 3), np.uint8), photometric="rgb")


def _zlib_tiled_float(path):
    tifffile.imwrite(
        path,
        np.zeros((3, 32, 48), np.float32),
        photometric="minisblack",
        compression="zlib",
        tile=(16, 16),
    )


def _png(path):
    imageio.imwrite(path, np.zeros((5, 6), np.uint8))


def _two_stacks(path):
    second = path.replace(".tif", "_2.tif")
    tifffile.imwrite(path, np.zeros((7, 5, 6), np.uint16), photometric="minisblack")
    tifffile.imwrite(second, np.zeros((3, 5, 6), np.uint16), photometric="minisblack")
    return [path, second]


@pytest.mark.parametrize(
    "write, name",
    [
        (_imagej_tzcyx, "imagej.tif"),
        (_ome_two_series, "two_series.ome.tif"),
        (_single_frame_stack, "one_frame.tif"),
        (_pages_without_shape_metadata, "pages.tif"),
        (_rgb, "rgb.tif"),
        (_zlib_tiled_float, "zlib_tiled.tif"),
        (_png, "fallback.png"),
        (_two_stacks, "two_stacks.tif"),
    ],
)
@pytest.mark.parametrize("headers", ["readable", "unreadable"])
def test_shape_and_output_path_match_a_full_read(
    tmp_path, monkeypatch, write, name, headers
):
    # tifffile and imageio are unpinned; this pins shape to what data returns
    path = str(tmp_path / name)
    image_data = ImageData(write(path) or path)
    data = image_data.data
    if headers == "unreadable":

        def unreadable(_path):
            raise ValueError("unreadable headers")

        monkeypatch.setattr(image_module, "_tiff_shape", unreadable)

    assert image_data.shape == data.shape
    # 3D and up is served as the first TIFF, below that as JSON
    output_path = image_data.output_path
    if data.ndim >= 3:
        served = len(ImageData(path).data)
        assert (output_path.path, output_path.max_index) == (path, served)
    else:
        assert (output_path.path, output_path.max_index) == (None, 1)


def _truncated(path):
    tifffile.imwrite(path, np.ones((10, 16, 16), np.uint16))
    with open(path, "r+b") as f:
        f.truncate(int(os.path.getsize(path) * 0.55))


def _description_claims_more_frames(path):
    with tifffile.TiffWriter(path) as tif:
        for i in range(3):
            tif.write(
                np.ones((5, 6), np.uint16),
                metadata=None,
                description='{"shape": [10, 5, 6]}' if i == 0 else None,
            )


def _lzw_without_its_codec(path):
    # marks the pages LZW-compressed: decoding then fails as it does for a
    # real LZW file when imagecodecs is not installed
    tifffile.imwrite(path, np.ones((5, 16, 16), np.uint16), metadata=None)
    with tifffile.TiffFile(path) as tif:
        offsets = [page.tags[259].offset for page in tif.pages]
    with open(path, "r+b") as f:
        for offset in offsets:
            f.seek(offset + 8)
            f.write(struct.pack("<H", 5))


@pytest.mark.parametrize(
    "write", [_truncated, _description_claims_more_frames, _lzw_without_its_codec]
)
def test_a_tiff_data_cannot_read_fails_as_it_does_for_data(tmp_path, write):
    # the headers alone would report a stack, and the node would turn green
    path = str(tmp_path / "broken.tif")
    write(path)
    image_data = ImageData(path)
    with pytest.raises(Exception) as from_data:
        image_data.data
    with pytest.raises(type(from_data.value)):
        image_data.output_path


def split_as_before(image_data, output_dir, lengths):
    """split_image as it was: data read whole, then each part written as a slice."""
    image = image_data.data
    path = image_data.path[0] if isinstance(image_data.path, list) else image_data.path
    name, ext = os.path.splitext(os.path.basename(path))
    os.makedirs(os.path.join(output_dir, "image_split", name))
    paths, offset = [], 0
    for n, length in enumerate(lengths):
        paths.append(os.path.join(output_dir, "image_split", name, f"{name}_{n}{ext}"))
        with tifffile.TiffWriter(paths[-1], bigtiff=True) as tif:
            tif.write(image[offset : offset + length])
        offset += length
    return paths


def _stack(path, frames=11, dtype=np.uint16, frame_shape=(5, 6), **kwargs):
    rng = np.random.default_rng(frames)
    movie = rng.normal(300, 60, (frames, *frame_shape)).clip(0).astype(dtype)
    tifffile.imwrite(path, movie, photometric="minisblack", **kwargs)
    return path


def _big_endian(path):
    return _stack(path, byteorder=">")


def _zlib_stack(path):
    return _stack(path, compression="zlib")


def _float64(path):
    return _stack(path, dtype=np.float64)


def _three_wide(path):
    return _stack(path, frame_shape=(5, 3))


def _mixed_dtypes(path):
    second = path.replace(".tif", "_2.tif")
    return [_stack(path, frames=7), _stack(second, frames=4, dtype=np.float32)]


def _three_stacks(path):
    return [_stack(path.replace(".tif", f"_{n}.tif"), frames=n + 3) for n in range(3)]


@pytest.mark.parametrize(
    "write, name",
    [
        (_stack, "stack.tif"),
        (_big_endian, "big_endian.tif"),
        (_zlib_stack, "zlib.tif"),  # decoded a batch of pages at a time
        (_zlib_tiled_float, "zlib_tiled.tif"),
        (_float64, "float64.tif"),
        (_three_wide, "three_wide.tif"),  # stored as RGB
        (_mixed_dtypes, "mixed.tif"),
        (_three_stacks, "three.tif"),
        (_two_stacks, "two_stacks.tif"),
        (_ome_two_series, "two_series.ome.tif"),
        (_pages_without_shape_metadata, "pages.tif"),
        (_rgb, "rgb.tif"),
        (_imagej_tzcyx, "imagej.tif"),  # read whole, as before
        (_png, "fallback.png"),  # read whole, as before
    ],
)
@pytest.mark.parametrize("split", ["halves", "thirds", "short parts"])
@pytest.mark.parametrize(
    "batch_bytes", [1, 64 << 20], ids=["frame batches", "file batches"]
)
def test_split_parts_are_byte_identical_to_slices_of_data(
    tmp_path, monkeypatch, write, name, split, batch_bytes
):
    path = str(tmp_path / name)
    image_data = ImageData(write(path) or path)
    frames = len(image_data.data)
    if split == "short parts":  # 3- and 4-frame parts are stored as RGB
        if frames < 8:
            pytest.skip("too few frames")
        lengths = [3, 4, frames - 7]
    else:
        n = 2 if split == "halves" else 3
        base, extra = divmod(frames, n)
        lengths = [base + 1 if i < extra else base for i in range(n)]
    # decoded files come a frame or a whole file at a time, so parts start and
    # end between batches or inside them; mapped files come whole
    monkeypatch.setattr(image_module, "BATCH_BYTES", batch_bytes)

    expected = split_as_before(image_data, str(tmp_path / "old"), lengths)
    if split == "short parts":
        actual = image_data.split_image(str(tmp_path / "new"), lengths=lengths)
    else:
        actual = image_data.split_image(str(tmp_path / "new"), n_files=len(lengths))

    assert [os.path.basename(p) for p in actual] == [
        os.path.basename(p) for p in expected
    ]
    for a, e in zip(actual, expected):
        assert open(a, "rb").read() == open(e, "rb").read(), os.path.basename(a)


@pytest.mark.parametrize("write", [_stack, _zlib_stack, _three_stacks])
def test_split_does_not_read_a_stack_whole(tmp_path, monkeypatch, write):
    paths = write(str(tmp_path / "movie.tif"))
    image_data = ImageData(paths)

    def no_full_read(*args, **kwargs):
        raise AssertionError("the whole stack was read")

    monkeypatch.setattr(ImageData, "data", property(no_full_read))
    monkeypatch.setattr(imageio, "volread", no_full_read)
    parts = image_data.split_image(str(tmp_path), n_files=2)
    assert sum(len(tifffile.imread(p)) for p in parts) == image_data.shape[0]


@pytest.mark.parametrize("write", [_stack, _pages_without_shape_metadata])
def test_split_still_works_where_files_cannot_be_mapped(tmp_path, monkeypatch, write):
    # each page is a frame, so pages are decoded a batch at a time, not read whole
    path = str(tmp_path / "movie.tif")
    image_data = ImageData(write(path) or path)
    lengths = [2, image_data.shape[0] - 2]
    expected = split_as_before(image_data, str(tmp_path / "old"), lengths)

    def cannot_map(*args, **kwargs):
        raise OSError("mmap is not supported on this mount")

    monkeypatch.setattr(tifffile, "memmap", cannot_map)
    monkeypatch.setattr(imageio, "volread", lambda *a, **k: pytest.fail("read whole"))
    actual = image_data.split_image(str(tmp_path / "new"), lengths=lengths)
    for a, e in zip(actual, expected):
        assert open(a, "rb").read() == open(e, "rb").read()


def test_a_split_that_fails_leaves_no_parts(tmp_path, monkeypatch):
    # a read error in the second part: the first part and the partial second go
    path = _stack(str(tmp_path / "movie.tif"))
    read = image_module.iter_frame_batches

    def fails_after_seven_frames(path, frames, batch_size):
        yield next(read(path, frames, frames))[:7]
        raise OSError("read error")

    monkeypatch.setattr(image_module, "iter_frame_batches", fails_after_seven_frames)
    out = tmp_path / "new"
    with pytest.raises(OSError, match="read error"):
        ImageData(path).split_image(str(out), lengths=[6, 5])
    assert list((out / "image_split" / "movie").iterdir()) == []


def test_split_reads_a_file_that_is_no_tiff_once(tmp_path, monkeypatch):
    path = str(tmp_path / "frames.png")
    _png(path)
    reads = []
    volread = imageio.volread
    monkeypatch.setattr(
        imageio, "volread", lambda *a, **k: reads.append(a) or volread(*a, **k)
    )
    ImageData(path).split_image(str(tmp_path / "new"), n_files=2)
    assert len(reads) == 1


def test_split_refuses_a_file_that_reads_fewer_frames_than_its_headers(
    tmp_path, monkeypatch
):
    image_data, _ = make_image_data(tmp_path, frames=10)
    read = image_module.iter_frame_batches

    def one_frame_short(path, frames, batch_size):
        yield next(read(path, frames, frames))[:-1]

    monkeypatch.setattr(image_module, "iter_frame_batches", one_frame_short)
    with pytest.raises(ValueError, match="read 9 frames, but its headers give 10"):
        image_data.split_image(str(tmp_path), n_files=2)


@pytest.mark.parametrize(
    "write, batches", [(_stack, [11]), (_zlib_stack, [4, 4, 3])], ids=["mapped", "zlib"]
)
def test_a_mapped_file_comes_as_one_view_decoded_pages_in_batches(
    tmp_path, write, batches
):
    # a view costs no memory, and walked frame by frame reads each page once
    path = write(str(tmp_path / "movie.tif"))
    read = list(image_module.iter_frame_batches(path, 11, 4))
    assert [len(batch) for batch in read] == batches
    assert isinstance(read[0], np.memmap) == (write is _stack)
    np.testing.assert_array_equal(np.concatenate(read), tifffile.imread(path))


def test_a_file_that_is_no_tiff_is_read_in_full(tmp_path):
    # tifffile.memmap raises TiffFileError there, which is no ValueError
    path = str(tmp_path / "frame.png")
    imageio.imwrite(path, np.arange(30, dtype=np.uint8).reshape(5, 6))
    expected = imageio.volread(path)
    read = list(image_module.iter_frame_batches(path, 5, 4))
    np.testing.assert_array_equal(np.concatenate(read), expected)
    np.testing.assert_array_equal(image_module.open_frames(path), expected)
