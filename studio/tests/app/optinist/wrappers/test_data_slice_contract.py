import inspect

import numpy as np
import pytest

from studio.app.common.dataclass import (
    BarData,
    HeatMapData,
    HTMLData,
    ScatterData,
    TimeSeriesData,
)
from studio.app.optinist.dataclass import FluoData
from studio.app.optinist.wrappers.data_utils.data_slice import data_slice
from studio.app.optinist.wrappers.data_utils.data_utils_utils import return_as_data_type

N_CELL, N_TIME = 4, 20


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def fluo():
    data = np.random.default_rng(0).random((N_CELL, N_TIME))
    return FluoData(data, index=np.arange(N_TIME), file_name="f")


def test_no_slicing_returns_mean_timeseries(fluo, output_dir):
    result = data_slice(fluo, output_dir, params={"slice_dims": []})

    assert "mean_timeseries" in result
    assert result["sliced_data"].data.shape == (N_CELL, N_TIME)
    np.testing.assert_allclose(
        result["mean_timeseries"].data[0], fluo.data.mean(axis=0)
    )


def test_missing_slice_dims_behaves_like_empty(fluo, output_dir):
    result = data_slice(fluo, output_dir, params={})

    assert result["sliced_data"].data.shape == (N_CELL, N_TIME)
    np.testing.assert_allclose(
        result["mean_timeseries"].data[0], fluo.data.mean(axis=0)
    )


def test_strided_slice_is_start_stop_step(fluo, output_dir):
    result = data_slice(fluo, output_dir, params={"slice_dims": [":", "0:10:2"]})

    assert result["sliced_data"].data.shape == (N_CELL, 5)
    np.testing.assert_allclose(result["sliced_data"].data, fluo.data[:, 0:10:2])
    np.testing.assert_allclose(
        result["mean_timeseries"].index, np.arange(N_TIME)[0:10:2]
    )


def test_zscore_normalization(fluo, output_dir):
    result = data_slice(
        fluo, output_dir, params={"slice_dims": [], "mean_normalization": "zscore"}
    )

    ts = result["mean_timeseries"].data[0]
    assert abs(ts.mean()) < 1e-10
    assert abs(ts.std() - 1) < 1e-10


def test_minmax_normalization(fluo, output_dir):
    result = data_slice(
        fluo, output_dir, params={"slice_dims": [], "mean_normalization": "minmax"}
    )

    ts = result["mean_timeseries"].data[0]
    assert ts.min() == 0
    assert ts.max() == 1


def test_missing_normalization_key_defaults_to_none(fluo, output_dir):
    result = data_slice(fluo, output_dir, params={"slice_dims": []})
    np.testing.assert_allclose(
        result["mean_timeseries"].data[0], fluo.data.mean(axis=0)
    )


def test_unknown_normalization_raises(fluo, output_dir):
    with pytest.raises(ValueError, match="mean_normalization"):
        data_slice(
            fluo,
            output_dir,
            params={"slice_dims": [], "mean_normalization": "median"},
        )


def test_normalization_value_is_case_insensitive(fluo, output_dir):
    result = data_slice(
        fluo, output_dir, params={"slice_dims": [], "mean_normalization": " Zscore "}
    )

    assert abs(result["mean_timeseries"].data[0].mean()) < 1e-10


def test_nan_sample_stays_nan_and_the_rest_is_normalized(output_dir):
    data = np.random.default_rng(5).random((N_CELL, N_TIME))
    data[0, 0] = np.nan
    fluo = FluoData(data, index=np.arange(N_TIME), file_name="f")
    result = data_slice(
        fluo, output_dir, params={"slice_dims": [], "mean_normalization": "zscore"}
    )

    ts = result["mean_timeseries"].data[0]
    assert np.isnan(ts[0])
    assert not np.isnan(ts[1:]).any()
    assert abs(np.nanmean(ts)) < 1e-10
    assert abs(np.nanstd(ts) - 1) < 1e-10


def test_constant_trace_normalizes_to_zeros(output_dir):
    fluo = FluoData(np.ones((N_CELL, N_TIME)), index=np.arange(N_TIME), file_name="f")
    result = data_slice(
        fluo, output_dir, params={"slice_dims": [], "mean_normalization": "zscore"}
    )

    ts = result["mean_timeseries"].data[0]
    assert not np.isnan(ts).any()
    np.testing.assert_allclose(ts, np.zeros(N_TIME))


def test_cell_zscore_stops_one_cell_dominating_the_mean(output_dir):
    rng = np.random.default_rng(8)
    data = rng.random((N_CELL, N_TIME))
    data[0] *= 1000  # one high-amplitude cell
    fluo = FluoData(data, index=np.arange(N_TIME), file_name="f")

    raw = data_slice(fluo, output_dir, params={"slice_dims": []})
    normed = data_slice(
        fluo, output_dir, params={"slice_dims": [], "cell_normalization": "zscore"}
    )

    raw_corr = np.corrcoef(raw["mean_timeseries"].data[0], data[0])[0, 1]
    normed_corr = np.corrcoef(normed["mean_timeseries"].data[0], data[0])[0, 1]
    assert raw_corr > 0.99
    assert normed_corr < raw_corr
    cells = normed["sliced_data"].data
    np.testing.assert_allclose(cells.mean(axis=1), 0, atol=1e-10)
    np.testing.assert_allclose(cells.std(axis=1), 1)
    assert normed["sliced_data"].std is None


def test_unknown_cell_normalization_raises(fluo, output_dir):
    with pytest.raises(ValueError, match="cell_normalization"):
        data_slice(fluo, output_dir, params={"cell_normalization": "minmax"})


def test_integer_spec_on_non_index_dim(fluo, output_dir):
    result = data_slice(fluo, output_dir, params={"slice_dims": ["1", ":"]})

    np.testing.assert_allclose(result["mean_timeseries"].data[0], fluo.data[1])
    np.testing.assert_allclose(result["mean_timeseries"].index, np.arange(N_TIME))


def test_integer_spec_on_index_dim_skips_mean_timeseries(fluo, output_dir):
    result = data_slice(fluo, output_dir, params={"slice_dims": [":", "5"]})

    assert "mean_timeseries" not in result
    np.testing.assert_allclose(result["sliced_data"].data[0], fluo.data[:, 5])


def test_timeseries_input_keeps_cell_numbers_and_sem(output_dir):
    rng = np.random.default_rng(1)
    ts = TimeSeriesData(
        rng.random((N_CELL, N_TIME)),
        std=rng.random((N_CELL, N_TIME)),
        sem=rng.random((N_CELL, N_TIME)),
        index=np.arange(N_TIME),
        cell_numbers=[1, 3, 4, 7],
        file_name="t",
    )
    result = data_slice(ts, output_dir, params={"slice_dims": ["1:3", "0:10"]})

    out = result["sliced_data"]
    assert type(out) is TimeSeriesData
    assert "mean_timeseries" in result
    assert list(out.cell_numbers) == [3, 4]
    np.testing.assert_allclose(out.std, ts.std[1:3, 0:10])
    np.testing.assert_allclose(out.sem, ts.sem[1:3, 0:10])


def test_scatter_input_round_trips_orientation(output_dir):
    proj = np.random.default_rng(3).random((10, 2))
    scatter = ScatterData(proj, file_name="s")
    result = data_slice(scatter, output_dir, params={"slice_dims": []})

    assert type(result["sliced_data"]) is ScatterData
    np.testing.assert_allclose(result["sliced_data"].data, scatter.data)


def test_heatmap_input_keeps_columns_and_row_labels(output_dir):
    hm = HeatMapData(
        np.random.default_rng(6).random((4, 5)),
        columns=[10, 20, 30, 40, 50],
        index=[1, 3, 4, 7],
        file_name="h",
    )
    result = data_slice(hm, output_dir, params={"slice_dims": ["0:2", ":"]})

    assert type(result["sliced_data"]) is HeatMapData
    assert list(result["sliced_data"].columns) == [10, 20, 30, 40, 50]
    assert list(result["sliced_data"].index) == [1, 3]
    assert "mean_timeseries" not in result


def test_heatmap_collapsed_to_1d_does_not_crash(output_dir):
    hm = HeatMapData(np.random.default_rng(7).random((4, 5)), file_name="h")
    result = data_slice(hm, output_dir, params={"slice_dims": ["2", ":"]})

    assert result["sliced_data"].data.shape == (1, 5)


def test_bar_input_slices_rows(output_dir):
    bar = BarData(np.random.default_rng(2).random((6, 5)), file_name="b")
    result = data_slice(bar, output_dir, params={"slice_dims": ["0:3", ":"]})

    assert type(result["sliced_data"]) is BarData
    np.testing.assert_allclose(result["sliced_data"].data, bar.data[0:3])


def test_bar_integer_column_keeps_one_value_per_row(output_dir):
    bar = BarData(np.random.default_rng(3).random((6, 5)), file_name="b")
    result = data_slice(bar, output_dir, params={"slice_dims": [":", "2"]})

    out = result["sliced_data"]
    assert out.data.shape == (6, 1)
    assert len(out.index) == 6
    np.testing.assert_allclose(out.data[:, 0], bar.data[:, 2])
    out.save_json(output_dir)


def test_heatmap_integer_column_keeps_row_labels(output_dir):
    hm = HeatMapData(
        np.random.default_rng(4).random((4, 5)), index=[1, 3, 4, 7], file_name="h"
    )
    result = data_slice(hm, output_dir, params={"slice_dims": [":", "1"]})

    out = result["sliced_data"]
    assert out.data.shape == (4, 1)
    assert list(out.index) == [1, 3, 4, 7]
    out.save_json(output_dir)


def test_heatmap_skips_cell_normalization(output_dir):
    hm = HeatMapData(
        np.random.default_rng(5).random((4, 5)), index=[1, 3, 4, 7], file_name="h"
    )
    result = data_slice(
        hm, output_dir, params={"slice_dims": [], "cell_normalization": "zscore"}
    )

    np.testing.assert_allclose(result["sliced_data"].data, hm.data)


def test_unsupported_input_type_raises(output_dir):
    html = HTMLData("<p>x</p>", file_name="h")
    with pytest.raises(ValueError, match="Unsupported data type"):
        return_as_data_type(html, np.zeros(3), output_dir, "out")


def test_declared_outputs_are_returned(fluo, output_dir):
    declared = set(inspect.signature(data_slice).return_annotation.keys())
    result = data_slice(fluo, output_dir, params={"slice_dims": []})
    assert declared <= set(result.keys())
