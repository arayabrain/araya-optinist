"""data_concat returns and saves the plot types return_as_data_type now builds."""

import numpy as np
import pytest

from studio.app.common.dataclass import (
    BarData,
    HeatMapData,
    ScatterData,
    TimeSeriesData,
)
from studio.app.optinist.dataclass import BehaviorData, IscellData
from studio.app.optinist.wrappers.data_utils.data_concat import data_concat

RNG = np.random.default_rng(0)


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


def _concat(a, b, output_dir):
    out = data_concat(a, b, output_dir, params={})["concatenated_data"]
    out.save_json(output_dir)
    return out


@pytest.mark.parametrize("cls", [BarData, HeatMapData, TimeSeriesData])
def test_rows_are_appended_and_saved(cls, output_dir):
    a, b = cls(RNG.random((3, 4)), file_name="a"), cls(
        RNG.random((2, 4)), file_name="b"
    )

    out = _concat(a, b, output_dir)

    assert type(out) is cls
    np.testing.assert_allclose(out.data, np.concatenate([a.data, b.data]))


def test_bar_row_labels_follow_the_appended_rows(output_dir):
    a = BarData(RNG.random((3, 2)), index=[10, 11, 12], file_name="a")
    b = BarData(RNG.random((2, 2)), index=[20, 21], file_name="b")

    out = _concat(a, b, output_dir)

    assert list(out.index) == [10, 11, 12, 20, 21]


def test_scatter_points_are_appended_and_saved(output_dir):
    p1, p2 = RNG.random((5, 2)), RNG.random((4, 2))

    out = _concat(
        ScatterData(p1, file_name="a"), ScatterData(p2, file_name="b"), output_dir
    )

    assert type(out) is ScatterData
    np.testing.assert_allclose(out.data, ScatterData(np.vstack([p1, p2])).data)


def test_axis_1_stacks_1d_inputs_as_behaviour_columns(output_dir):
    position, speed, trial = RNG.random(6), RNG.random(6), np.arange(6.0)

    two = data_concat(
        IscellData(position), IscellData(speed), output_dir, params={"axis": 1}
    )["concatenated_data"]
    three = _concat(two, IscellData(trial), output_dir)
    three_first = data_concat(IscellData(trial), two, output_dir, params={"axis": 1})[
        "concatenated_data"
    ]
    three_first.save_json(output_dir)

    assert type(two) is BehaviorData and type(three) is BehaviorData
    np.testing.assert_array_equal(three.data, np.column_stack([position, speed, trial]))
    assert three.output_path.data_shape == [6, 3]
    np.testing.assert_array_equal(three_first.data[:, 0], trial)
    assert three_first.output_path.data_shape == [6, 3]


def test_1d_inputs_on_axis_0_are_still_appended(output_dir):
    out = data_concat(
        IscellData(np.ones(6)), IscellData(np.zeros(6)), output_dir, params={}
    )["concatenated_data"]

    assert type(out) is IscellData and out.data.shape == (12,)
