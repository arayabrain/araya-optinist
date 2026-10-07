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
