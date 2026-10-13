import numpy as np
import pytest
import tifffile

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
