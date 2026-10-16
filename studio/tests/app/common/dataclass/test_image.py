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
