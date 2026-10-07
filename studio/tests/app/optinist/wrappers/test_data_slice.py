import numpy as np
import pytest

from studio.app.common.core.logger import AppLogger
from studio.app.optinist.dataclass import FluoData
from studio.app.optinist.wrappers.data_utils.data_slice import data_slice

# init logging config at import so dictConfig cannot replace caplog's handler mid-test
AppLogger.get_logger()

N_CELL, N_TIME = 2, 8


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def fluo():
    return FluoData(
        np.random.default_rng(0).random((N_CELL, N_TIME)),
        file_name="fluo",
    )


def test_three_part_spec_is_start_stop_step(fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "0:8:2"]})
    np.testing.assert_array_equal(result["sliced_data"].data, fluo.data[:, 0:8:2])
    assert "selects" not in caplog.text


def test_swapped_stop_and_step_warns(fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "0:2:8"]})
    assert result["sliced_data"].data.shape == (N_CELL, 1)
    assert "selects 1 of 8" in caplog.text
    assert "did you mean '0:8:2'?" in caplog.text


def test_swapped_with_nonzero_start_warns(fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "2:2:6"]})
    assert result["sliced_data"].data.shape == (0, 0)
    assert "selects 0 of 8" in caplog.text
    assert "did you mean '2:6:2'?" in caplog.text


def test_deliberate_single_element_slice_does_not_warn(fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "0:1:1"]})
    assert result["sliced_data"].data.shape == (N_CELL, 1)
    assert "selects" not in caplog.text


def test_reverse_slice_to_zero_does_not_warn(fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "1:0:-1"]})
    assert result["sliced_data"].data.shape == (N_CELL, 1)
    assert "selects" not in caplog.text


def test_empty_parts_do_not_warn(output_dir, caplog):
    fluo = FluoData(np.random.default_rng(1).random((N_CELL, 2)), file_name="fluo")
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "::2"]})
    assert result["sliced_data"].data.shape == (N_CELL, 1)
    assert "selects" not in caplog.text


def test_size_one_dimension_does_not_warn(output_dir, caplog):
    fluo = FluoData(np.random.default_rng(2).random((N_CELL, 1)), file_name="fluo")
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "0:1:1"]})
    assert result["sliced_data"].data.shape == (N_CELL, 1)
    assert "selects" not in caplog.text


def test_empty_result_warns(fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "10:20"]})
    # TimeSeriesData rewrites empty data to shape (0, len(index)), collapsing cells
    assert result["sliced_data"].data.shape == (0, 0)
    assert "selects 0 of 8" in caplog.text


def test_zero_step_still_fails_loudly(fluo, output_dir):
    with pytest.raises(ValueError, match="Data slicing failed"):
        data_slice(fluo, output_dir, params={"slice_dims": [":", "0:8:0"]})


def test_too_many_parts_keeps_dimension(fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "1:2:3:4"]})
    np.testing.assert_array_equal(result["sliced_data"].data, fluo.data)
    assert "Invalid slice format" in caplog.text


def test_non_numeric_parts_keep_dimension(fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(fluo, output_dir, params={"slice_dims": [":", "a:b"]})
    np.testing.assert_array_equal(result["sliced_data"].data, fluo.data)
    assert "Could not parse slice spec" in caplog.text


def test_two_part_spec_is_start_stop(fluo, output_dir):
    result = data_slice(fluo, output_dir, params={"slice_dims": [":", "2:6"]})
    np.testing.assert_array_equal(result["sliced_data"].data, fluo.data[:, 2:6])


def test_comma_separated_string_slice_dims(fluo, output_dir):
    result = data_slice(fluo, output_dir, params={"slice_dims": ":, 0:8:2"})
    assert result["sliced_data"].data.shape == (N_CELL, 4)
