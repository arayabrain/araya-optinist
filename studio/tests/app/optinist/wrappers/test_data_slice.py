import tracemalloc

import numpy as np
import pytest

from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import CsvData, ImageData, TimeSeriesData
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.data_utils.data_slice import _dff as dff_rows
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


def _dff(data, output_dir, **params):
    params = {"slice_dims": [], "cell_normalization": "dff", **params}
    return data_slice(data, output_dir, params=params)


def _trace(n=200):
    t = np.arange(n)
    rng = np.random.default_rng(11)
    return 10 + np.sin(t / 15) + rng.random(n) + 5 * (t % 50 == 0)


def _sliding_percentile(row, window, q):
    half = window // 2
    padded = np.pad(row, half, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.nanpercentile(windows, q, axis=-1)


def _maximin(x, window, sigma):
    from scipy.ndimage import gaussian_filter1d, maximum_filter1d, minimum_filter1d

    f0 = gaussian_filter1d(x, sigma, axis=-1)
    return maximum_filter1d(minimum_filter1d(f0, window, axis=-1), window, axis=-1)


def test_dff_percentile_whole_recording(output_dir):
    x = np.random.default_rng(10).random((3, 40)) + 1.0
    result = _dff(FluoData(x, file_name="f"), output_dir)

    p8 = np.percentile(x, 8, axis=1, keepdims=True)
    np.testing.assert_allclose(result["sliced_data"].data, (x - p8) / np.abs(p8))


def test_dff_negative_baseline_divides_by_abs(output_dir):
    x = -5 + 0.5 * np.random.default_rng(14).random((1, 40))
    out = _dff(FluoData(x, file_name="f"), output_dir)["sliced_data"].data

    p8 = np.percentile(x, 8, axis=1, keepdims=True)
    assert (p8 < 0).all()
    np.testing.assert_allclose(out, (x - p8) / np.abs(p8))


@pytest.mark.parametrize("q", [8, 50])
def test_dff_percentile_sliding_window_pads_with_edge(q, output_dir):
    x = np.array([[5.0, 1.0, 4.0, 2.0, 8.0, 3.0, 7.0, 6.0]])
    result = _dff(
        FluoData(x, file_name="f"), output_dir, f0_window_frames=5, f0_percentile=q
    )

    f0 = _sliding_percentile(x[0], 5, q)
    np.testing.assert_allclose(result["sliced_data"].data[0], (x[0] - f0) / f0)


def test_dff_percentile_even_window_takes_the_extra_sample_before(output_dir):
    x = np.array([[5.0, 1.0, 4.0, 2.0, 8.0, 3.0, 7.0, 6.0]])
    result = _dff(
        FluoData(x, file_name="f"), output_dir, f0_window_frames=4, f0_percentile=50
    )

    # median of x[i - 2 : i + 2], edge padded
    f0 = np.array([5.0, 4.5, 3.0, 3.0, 3.5, 5.0, 6.5, 6.0])
    np.testing.assert_allclose(result["sliced_data"].data[0], (x[0] - f0) / f0)


def test_dff_sigma_longer_than_trace_is_clamped(output_dir, caplog):
    fluo = FluoData(np.random.default_rng(16).random((2, 40)) + 1.0, file_name="f")
    maximin = {"f0_method": "maximin", "f0_window_frames": 5}
    at_length = _dff(fluo, output_dir, f0_sigma_frames=40, **maximin)
    with caplog.at_level("WARNING"):
        huge = _dff(fluo, output_dir, f0_sigma_frames=1e8, **maximin)

    np.testing.assert_allclose(huge["sliced_data"].data, at_length["sliced_data"].data)
    assert "f0_sigma_frames 1e+08 > trace length 40; using 40" in caplog.text


def test_dff_window_longer_than_trace_is_clamped(output_dir, caplog):
    x = np.random.default_rng(15).random((2, 40)) + 1.0
    fluo = FluoData(x, file_name="f")
    whole = _dff(fluo, output_dir)["sliced_data"].data
    with caplog.at_level("WARNING"):
        clamped = _dff(fluo, output_dir, f0_window_frames=100)["sliced_data"].data

    np.testing.assert_allclose(clamped, whole)
    assert "f0_window_frames 100 >= trace length 40; using 40" in caplog.text


def test_dff_maximin_matches_scipy_composition(output_dir):
    x = np.stack([_trace(), _trace()[::-1]])
    result = _dff(
        FluoData(x, file_name="f"),
        output_dir,
        f0_method="maximin",
        f0_window_frames=6,
        f0_sigma_frames=1.5,
    )

    f0 = _maximin(x, 6, 1.5)
    np.testing.assert_allclose(result["sliced_data"].data, (x - f0) / np.abs(f0))


def test_dff_maximin_matches_suite2p_preprocess(output_dir):
    dcnv = pytest.importorskip("suite2p.extraction.dcnv")
    x = np.stack([_trace(), _trace()[::-1]])
    result = _dff(
        FluoData(x, file_name="f"),
        output_dir,
        f0_method="maximin",
        f0_window_frames=6,
        f0_sigma_frames=1.5,
    )

    f0 = x - dcnv.preprocess(
        x.copy(), baseline="maximin", win_baseline=6, sig_baseline=1.5, fs=1
    )
    np.testing.assert_allclose(
        result["sliced_data"].data, (x - f0) / np.abs(f0), atol=1e-6
    )


def test_dff_keeps_float32(output_dir):
    x = (np.random.default_rng(16).random((2, 30)) + 1.0).astype(np.float32)
    out = _dff(FluoData(x, file_name="f"), output_dir)["sliced_data"].data
    assert out.dtype == np.float32


@pytest.mark.parametrize("method", ["percentile", "maximin"])
def test_dff_sliding_baseline_peaks_below_three_input_copies(method):
    x = 2 + np.random.default_rng(18).random((64, 10000), dtype=np.float32)
    x[::4, 5000] = np.nan
    tracemalloc.start()
    try:
        dff_rows(x, 1, method, 50, 8.0, 10.0)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak < 3 * x.nbytes


@pytest.mark.parametrize(
    "row",
    [
        np.zeros(20),
        np.r_[np.zeros(18), 5.0, 7.0],
        np.r_[np.full(10, 1e-12), np.linspace(0.5, 1.0, 10)],
    ],
    ids=["all-zero", "zero-f0-nonzero-samples", "f0-1e-12"],
)
def test_dff_zero_baseline_row_is_nan_with_warning(row, output_dir, caplog):
    x = np.vstack([row, np.arange(1.0, 21.0)])
    with caplog.at_level("WARNING"):
        result = _dff(FluoData(x, file_name="f"), output_dir)

    out = result["sliced_data"].data
    assert np.isnan(out[0]).all()
    assert np.isfinite(out[1]).all()
    assert "F0 is near 0 in 1 row(s)" in caplog.text
    assert "leaves out 1 all-NaN dff row(s)" in caplog.text
    np.testing.assert_allclose(result["mean_timeseries"].data[0], out[1])


def test_dff_mean_skips_a_partly_floored_row_only_where_it_is_nan(output_dir):
    x = np.vstack([np.ones(100), 2 + np.random.default_rng(17).random((4, 100))])
    x[0, 40:60] = 0
    result = _dff(FluoData(x, file_name="f"), output_dir, f0_window_frames=11)

    out = result["sliced_data"].data
    mean = result["mean_timeseries"].data[0]
    floored = np.isnan(out[0])
    assert floored[40:60].all() and not floored.all()
    np.testing.assert_allclose(mean[floored], out[1:, floored].mean(axis=0))
    np.testing.assert_allclose(mean[~floored], out[:, ~floored].mean(axis=0))


def test_dff_leaves_behavior_data_unchanged(output_dir, caplog):
    behavior = BehaviorData(np.random.default_rng(12).random((N_TIME, 3)))
    with caplog.at_level("WARNING"):
        result = _dff(behavior, output_dir)

    np.testing.assert_array_equal(result["sliced_data"].data, behavior.data)
    assert "cell_normalization is not applied" in caplog.text


def test_dff_drops_std_and_sem(output_dir):
    x = np.random.default_rng(13).random((N_CELL, N_TIME)) + 1.0
    ts = TimeSeriesData(x, std=x * 0.1, sem=x * 0.01, file_name="t")

    kept = data_slice(ts, output_dir, params={"slice_dims": []})["sliced_data"]
    dropped = _dff(ts, output_dir)["sliced_data"]
    assert kept.std is not None and kept.sem is not None
    assert dropped.std is None and dropped.sem is None


@pytest.mark.parametrize(
    "params",
    [{}, {"f0_window_frames": 5}, {"f0_method": "maximin", "f0_window_frames": 6}],
    ids=["percentile-whole", "percentile-window", "maximin"],
)
def test_dff_nan_sample_stays_nan(params, output_dir):
    x = _trace(60)[None, :].copy()
    x[0, 30] = np.nan
    out = _dff(FluoData(x, file_name="f"), output_dir, **params)["sliced_data"].data

    assert np.isnan(out[0, 30])
    assert np.isfinite(np.delete(out[0], 30)).all()


def test_dff_percentile_nan_row_matches_nan_aware_reference(output_dir):
    x = _trace(80)[None, :].copy()
    x[0, 40] = np.nan
    out = _dff(FluoData(x, file_name="f"), output_dir, f0_window_frames=9)

    f0 = _sliding_percentile(x[0], 9, 8)
    np.testing.assert_allclose(out["sliced_data"].data[0], (x[0] - f0) / f0)


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_dff_maximin_nonfinite_row_matches_away_from_the_gap(bad, output_dir):
    window, sigma, gap = 20, 3.0, 200
    clean = _trace(400)
    x = clean[None, :].copy()
    x[0, gap] = bad
    out = _dff(
        FluoData(x, file_name="f"),
        output_dir,
        f0_method="maximin",
        f0_window_frames=window,
        f0_sigma_frames=sigma,
    )["sliced_data"].data[0]

    reach = int(4 * sigma) + 2 * window
    far = np.r_[reach : gap - reach, gap + reach + 1 : 400 - reach]
    f0 = _maximin(clean, window, sigma)
    np.testing.assert_allclose(out[far], ((clean - f0) / np.abs(f0))[far])
    assert np.isfinite(np.delete(out, gap)).all()


@pytest.mark.parametrize(
    "params",
    [{}, {"f0_window_frames": 3}, {"f0_method": "maximin", "f0_window_frames": 3}],
)
def test_dff_on_an_empty_time_slice_returns_it_empty(params, fluo, output_dir, caplog):
    with caplog.at_level("WARNING"):
        result = data_slice(
            fluo,
            output_dir,
            params={
                "slice_dims": [":", "10:20"],
                "cell_normalization": "dff",
                **params,
            },
        )

    assert result["sliced_data"].data.size == 0
    assert "selects 0 of 8" in caplog.text
    assert "all-NaN" not in caplog.text


@pytest.mark.parametrize(
    "params, match",
    [
        ({"f0_method": "median"}, "f0_method"),
        ({"f0_method": "maximin", "f0_window_frames": 0}, "maximin needs"),
        ({"f0_window_frames": -1}, "f0_window_frames"),
        ({"f0_window_frames": 2.5}, "f0_window_frames"),
        ({"f0_percentile": 101}, "f0_percentile"),
        ({"f0_sigma_frames": 0}, "f0_sigma_frames"),
        ({"f0_sigma_frames": float("inf")}, "f0_sigma_frames"),
    ],
    ids=[
        "unknown-method",
        "maximin-window-0",
        "negative-window",
        "float",
        "pct>100",
        "sigma-0",
        "sigma-inf",
    ],
)
def test_dff_invalid_params_raise(params, match, fluo, output_dir):
    with pytest.raises(ValueError, match=match):
        _dff(fluo, output_dir, **params)
