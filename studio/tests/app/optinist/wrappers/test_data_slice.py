import numpy as np
import pytest

from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import CsvData, ImageData
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
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
    assert result["sliced_data"].data.size == 0
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
    assert result["sliced_data"].data.size == 0
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


@pytest.mark.parametrize(
    "make_data, slice_dims, expected_shape",
    [
        (lambda d: IscellData(np.array([1, 0, 1, 1])), ["0:2:4"], (1,)),
        (
            lambda d: ImageData(
                np.random.default_rng(3).random((6, 4, 5)), output_dir=d
            ),
            ["0:2:6", ":", ":"],
            (1, 4, 5),
        ),
    ],
    ids=["iscell_1d", "image_3d"],
)
def test_swap_hint_on_axis_0(make_data, slice_dims, expected_shape, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(
            make_data(output_dir), output_dir, params={"slice_dims": slice_dims}
        )
    assert result["sliced_data"].data.shape == expected_shape
    assert "on axis 0 (0-based)" in caplog.text
    assert "did you mean" in caplog.text


def test_csv_input_returns_behavior_data(output_dir):
    csv = CsvData(np.random.default_rng(4).random((N_TIME, 3)), params={})
    result = data_slice(csv, output_dir, params={"slice_dims": ["0:8:2", ":"]})
    assert isinstance(result["sliced_data"], BehaviorData)
    np.testing.assert_array_equal(result["sliced_data"].data, csv.data[0:8:2])
