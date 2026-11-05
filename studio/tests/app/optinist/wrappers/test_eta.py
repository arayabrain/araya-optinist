import inspect
import logging
import warnings

import numpy as np
import pytest

from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.eta import (
    ETA,
    baseline_zscore,
)

N_TIME, N_CELL = 100, 3
PARAMS = {
    "transpose_x": False,
    "transpose_y": False,
    "event_col_index": 1,
    "trigger_type": "up",
    "trigger_threshold": 0.5,
    "pre_event": -2,
    "post_event": 2,
}
WINDOW = abs(PARAMS["pre_event"]) + 3 + PARAMS["post_event"]  # trigger_len == 3


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def neural():
    return FluoData(np.random.default_rng(0).random((N_TIME, N_CELL)), file_name="f")


@pytest.fixture
def behavior():
    y = np.zeros((N_TIME, 2))
    for start in (10, 30, 50):
        y[start : start + 3, 1] = 1.0
    return BehaviorData(y, file_name="b")


def test_mean_trace_is_average_across_cells(neural, behavior, output_dir):
    info = ETA(neural, behavior, output_dir, params=dict(PARAMS))

    assert info["mean"].data.shape == (N_CELL, WINDOW)
    assert info["mean_trace"].data.shape == (1, WINDOW)
    np.testing.assert_allclose(
        info["mean_trace"].data[0], info["mean"].data.mean(axis=0)
    )
    np.testing.assert_allclose(info["mean_trace"].index, info["mean"].index)


def test_mean_trace_averages_only_the_iscell_cells(neural, behavior, output_dir):
    info = ETA(
        neural,
        behavior,
        output_dir,
        iscell=IscellData(np.array([1, 0, 1])),
        params=dict(PARAMS),
    )
    all_cells = ETA(neural, behavior, output_dir, params=dict(PARAMS))

    assert info["mean"].data.shape == (2, WINDOW)
    np.testing.assert_allclose(
        info["mean_trace"].data[0], all_cells["mean"].data[[0, 2]].mean(axis=0)
    )


def test_mean_trace_band_default_is_sem(neural, behavior, output_dir):
    info = ETA(neural, behavior, output_dir, params=dict(PARAMS))

    expected_sem = info["mean"].data.std(axis=0, ddof=1) / np.sqrt(N_CELL)
    np.testing.assert_allclose(info["mean_trace"].std[0], expected_sem)


def test_mean_trace_band_std_option(neural, behavior, output_dir):
    info = ETA(
        neural, behavior, output_dir, params={**PARAMS, "mean_trace_band": "std"}
    )

    np.testing.assert_allclose(
        info["mean_trace"].std[0], info["mean"].data.std(axis=0, ddof=1)
    )


def test_mean_trace_band_is_case_insensitive(neural, behavior, output_dir):
    info = ETA(
        neural, behavior, output_dir, params={**PARAMS, "mean_trace_band": " Std "}
    )

    np.testing.assert_allclose(
        info["mean_trace"].std[0], info["mean"].data.std(axis=0, ddof=1)
    )


def test_unknown_mean_trace_band_raises(neural, behavior, output_dir):
    with pytest.raises(ValueError, match="mean_trace_band"):
        ETA(
            neural,
            behavior,
            output_dir,
            params={**PARAMS, "mean_trace_band": "stdev"},
        )


def test_single_cell_band_is_nan(behavior, output_dir):
    neural = FluoData(np.random.default_rng(4).random((N_TIME, 1)), file_name="f")
    info = ETA(neural, behavior, output_dir, params=dict(PARAMS))

    assert info["mean_trace"].data.shape == (1, WINDOW)
    assert np.isnan(info["mean_trace"].std[0]).all()


def test_declared_outputs_are_returned(neural, behavior, output_dir):
    declared = set(inspect.signature(ETA).return_annotation.keys())
    info = ETA(neural, behavior, output_dir, params=dict(PARAMS))
    assert declared <= set(info.keys())


def test_mean_trace_in_nwb_postprocess(neural, behavior, output_dir):
    from studio.app.optinist.core.nwb.nwb import NWBDATASET

    info = ETA(neural, behavior, output_dir, params=dict(PARAMS))
    (post,) = info["nwbfile"][NWBDATASET.POSTPROCESS].values()
    np.testing.assert_allclose(post["mean_trace"], info["mean_trace"].data[0])
    np.testing.assert_allclose(post["mean_trace_band"], info["mean_trace"].std[0])


STARTS = (10, 30, 50)
BASELINE_PARAMS = {**PARAMS, "pre_event": -4, "baseline_window": 3}


def _windows(x, pre=4, post=2, trigger_len=3):
    return np.stack([x[s - pre : s + trigger_len + post] for s in STARTS])


def _baseline_z(windows, onset=4, baseline_window=3):
    base = windows[:, onset - baseline_window : onset]
    return (windows - base.mean(axis=1, keepdims=True)) / base.std(
        axis=1, keepdims=True
    )


def _eta(x, output_dir, iscell=None, **params):
    y = np.zeros((len(x), 2))
    for start in STARTS:
        y[start : start + 3, 1] = 1.0
    return ETA(
        FluoData(x, file_name="f"),
        BehaviorData(y, file_name="b"),
        output_dir,
        iscell=iscell,
        params={**BASELINE_PARAMS, **params},
    )


def test_baseline_is_the_frames_just_before_the_onset(neural, output_dir):
    # baseline_window < abs(pre_event), so a window-start baseline would differ
    info = _eta(neural.data, output_dir)

    z = _baseline_z(_windows(neural.data))
    np.testing.assert_allclose(info["mean"].data, z.mean(axis=0).T)
    np.testing.assert_allclose(info["mean"].std, z.std(axis=0, ddof=1).T)
    np.testing.assert_allclose(
        info["mean"].sem, z.std(axis=0, ddof=1).T / np.sqrt(len(STARTS))
    )
    np.testing.assert_allclose(
        info["mean_trace"].data[0], info["mean"].data.mean(axis=0)
    )
    assert list(info["mean"].index) == list(range(-4, 5))


@pytest.mark.parametrize(
    "baseline", [[0.5, 0.5, 0.5], [0.5, 0.5, 0.5 + 1e-12]], ids=["flat", "near_flat"]
)
def test_constant_baseline_skips_that_event_for_that_cell_only(
    neural, output_dir, baseline
):
    x = neural.data.copy()
    x[STARTS[0] - 3 : STARTS[0], 1] = baseline  # event 0, cell 1
    info = _eta(x, output_dir)

    z = _baseline_z(_windows(neural.data))  # events 1 and 2 are unchanged
    untouched = z.mean(axis=0).T
    z[0, :, 1] = np.nan
    np.testing.assert_allclose(info["mean"].data, np.nanmean(z, axis=0).T)
    np.testing.assert_allclose(info["mean"].data[1], z[1:, :, 1].mean(axis=0))
    np.testing.assert_allclose(info["mean"].data[[0, 2]], untouched[[0, 2]])
    np.testing.assert_allclose(info["mean"].sem[1], info["mean"].std[1] / np.sqrt(2))
    assert not np.isnan(info["mean_trace"].data).any()


@pytest.mark.parametrize(
    "baseline",
    [[np.nan, np.nan, np.nan], [0.5, np.nan, np.nan]],
    ids=["all_nan", "one_finite_sample"],
)
def test_a_baseline_without_spread_is_skipped_never_divided(baseline):
    data = np.random.default_rng(0).random((2, 9, 3))  # (event, frame, cell)
    data[0, 1:4, 1] = baseline

    z, skip = baseline_zscore(data, onset=4, baseline_window=3)

    assert skip.tolist() == [[False, True, False], [False, False, False]]
    assert np.isnan(z[0, :, 1]).all()
    assert np.isfinite(z[0][:, [0, 2]]).all() and np.isfinite(z[1]).all()


def test_a_float32_input_stays_float32_and_its_constant_baseline_is_skipped():
    data = np.random.default_rng(0).random((2, 160, 4)).astype(np.float32)
    data[0, 5:155, 1] = 0.1

    z, skip = baseline_zscore(data, onset=155, baseline_window=150)

    assert z.dtype == np.float32
    assert skip.tolist() == [[False, True, False, False], [False] * 4]


def test_skipped_pairs_are_logged_with_iscell_cell_numbers(neural, output_dir, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    x = neural.data.copy()
    x[STARTS[0] - 3 : STARTS[0], 2] = 0.5

    _eta(x, output_dir, iscell=IscellData(np.array([1, 0, 1])))

    assert (
        "1 event-cell pairs skipped for a constant or non-finite baseline, cells [2]"
        in caplog.text
    )
    assert "NaN samples" not in caplog.text


def test_input_nan_is_excluded_per_time_point(neural, output_dir, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    x = neural.data.copy()
    x[STARTS[1] + 1, 0] = np.nan  # event 1, cell 0, window position 5

    info = _eta(x, output_dir)

    z = _baseline_z(_windows(neural.data))
    expected = z.mean(axis=0).T
    expected[0, 5] = z[[0, 2], 5, 0].mean()
    np.testing.assert_allclose(info["mean"].data, expected)
    assert "NaN samples in 1 event windows are excluded per time point" in caplog.text
    assert "skipped" not in caplog.text


def test_time_point_with_no_event_left_blanks_only_that_heatmap_cell(
    neural, output_dir
):
    x = neural.data.copy()
    x[[s + 1 for s in STARTS], 0] = np.nan  # window position 5 in every event

    info = _eta(x, output_dir)

    row = info["mean"].data[0]
    heat = info["mean_heatmap"].data[0]
    assert np.isnan(row[5]) and np.isnan(heat[5])
    finite = np.delete(row, 5)
    np.testing.assert_allclose(
        np.delete(heat, 5), (finite - finite.min()) / np.ptp(finite)
    )


def test_raw_average_nan_blanks_only_that_heatmap_cell(neural, behavior, output_dir):
    x = neural.data.copy()
    x[STARTS[1] + 1, 0] = np.nan  # event 1, cell 0, window position 3

    info = ETA(FluoData(x, file_name="f"), behavior, output_dir, params=dict(PARAMS))

    row = info["mean"].data[0]
    heat = info["mean_heatmap"].data[0]
    assert np.isnan(row[3]) and np.isnan(heat[3])
    finite = np.delete(row, 3)
    np.testing.assert_allclose(
        np.delete(heat, 3), (finite - finite.min()) / np.ptp(finite)
    )


def test_cell_with_no_event_left_is_nan_without_warnings(neural, output_dir, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    x = neural.data.copy()
    for start in STARTS:
        x[start - 3 : start, 1] = 0.5

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        info = _eta(x, output_dir, sort_by_peak=True)

    assert np.isnan(info["mean"].data[1]).all()
    assert np.isfinite(info["mean"].data[[0, 2]]).all()
    np.testing.assert_allclose(
        info["mean_trace"].data[0], info["mean"].data[[0, 2]].mean(axis=0)
    )
    np.testing.assert_allclose(
        info["mean_trace"].std[0],
        info["mean"].data[[0, 2]].std(axis=0, ddof=1) / np.sqrt(2),
    )
    assert "cells [1] have no event left" in caplog.text


@pytest.mark.parametrize("baseline_window", [5, 1, -1, 2.5])
def test_invalid_baseline_window_raises(neural, output_dir, baseline_window):
    with pytest.raises(ValueError, match="baseline_window"):
        _eta(neural.data, output_dir, baseline_window=baseline_window)


def test_whole_float_baseline_window_is_accepted(neural, output_dir):
    as_float = _eta(neural.data, output_dir, baseline_window=3.0)
    as_int = _eta(neural.data, output_dir, baseline_window=3)

    np.testing.assert_array_equal(as_float["mean"].data, as_int["mean"].data)


@pytest.mark.parametrize("post_event", [-1, -3])
def test_baseline_window_needs_the_window_to_reach_the_onset(
    neural, output_dir, post_event
):
    # cross has no event length, so a negative post_event ends before the onset
    with pytest.raises(ValueError, match="reach the onset"):
        _eta(
            neural.data,
            output_dir,
            trigger_type="cross",
            post_event=post_event,
            baseline_window=2,
        )


@pytest.mark.parametrize("dtype", [np.float64, np.float32])
@pytest.mark.parametrize("params", [PARAMS, {**PARAMS, "baseline_window": 0}])
def test_baseline_window_off_averages_the_raw_windows(
    neural, behavior, output_dir, params, dtype
):
    x = neural.data.astype(dtype)
    info = ETA(FluoData(x, file_name="f"), behavior, output_dir, params=dict(params))

    windows = _windows(x, pre=2)
    mean = windows.mean(axis=0)
    std = windows.std(axis=0, ddof=1)
    sem = std / np.sqrt(len(STARTS))
    band = mean.T.std(axis=0, ddof=1) / np.sqrt(N_CELL)
    for got, want in [
        (info["mean"].data, mean.T),
        (info["mean"].std, std.T),
        (info["mean"].sem, sem.T),
        (info["mean_trace"].data[0], mean.T.mean(axis=0)),
        (info["mean_trace"].std[0], band),
    ]:
        assert got.dtype == want.dtype
        np.testing.assert_array_equal(got, want)
    assert info["mean"].data.dtype == dtype


def _peaked_neural(peak_offsets):
    # each cell fires once per event, peak_offsets frames after the onset
    x = np.zeros((N_TIME, len(peak_offsets)))
    for start in STARTS:
        for c, offset in enumerate(peak_offsets):
            x[start + offset, c] = 1.0
    return FluoData(x, file_name="f")


def test_order_from_overrides_sort_by_peak(behavior, output_dir):
    from studio.app.common.dataclass import HeatMapData

    reference = HeatMapData(np.zeros((3, 2)), index=[2, 0, 1])

    info = ETA(
        _peaked_neural([2, 1, 0]),
        behavior,
        output_dir,
        order_from=reference,
        params={**PARAMS, "sort_by_peak": True},
    )

    assert info["mean_heatmap"].index == [2, 0, 1]
    assert list(np.argmax(info["mean_heatmap"].data, axis=1)) == [2, 4, 3]


def test_order_from_matches_iscell_cell_numbers(behavior, output_dir):
    from studio.app.common.dataclass import HeatMapData

    reference = HeatMapData(np.zeros((4, 2)), index=[7, 2, 0, 1])

    info = ETA(
        _peaked_neural([2, 1, 0]),
        behavior,
        output_dir,
        iscell=IscellData(np.array([1, 0, 1])),
        order_from=reference,
        params={**PARAMS, "sort_by_peak": True},
    )

    assert info["mean_heatmap"].index == [2, 0]


def test_order_from_missing_a_cell_fails_with_a_clear_message(
    neural, behavior, output_dir
):
    from studio.app.common.dataclass import HeatMapData

    with pytest.raises(AssertionError, match="no row for 1 of 3 cells"):
        ETA(
            neural,
            behavior,
            output_dir,
            order_from=HeatMapData(np.zeros((2, 2)), index=[0, 1]),
            params=dict(PARAMS),
        )


def test_smoothing_equals_scipy_on_each_window(neural, behavior, output_dir):
    from scipy.ndimage import gaussian_filter1d

    info = ETA(
        neural, behavior, output_dir, params={**PARAMS, "smooth_sigma_frames": 1.5}
    )

    windows = _windows(neural.data, pre=2)
    smoothed = gaussian_filter1d(
        windows, 1.5, axis=1, mode="constant"
    ) / gaussian_filter1d(np.ones_like(windows), 1.5, axis=1, mode="constant")
    np.testing.assert_allclose(info["mean"].data, smoothed.mean(axis=0).T)
    np.testing.assert_allclose(info["mean"].std, smoothed.std(axis=0, ddof=1).T)
    assert info["mean_heatmap"].meta.title == "n = 3 events, smoothed 1.5 frames"


def test_smoothing_keeps_float32(behavior, output_dir):
    x = np.random.default_rng(1).random((N_TIME, N_CELL)).astype(np.float32)
    info = ETA(
        FluoData(x, file_name="f"),
        behavior,
        output_dir,
        params={**PARAMS, "smooth_sigma_frames": 1},
    )
    assert info["mean"].data.dtype == np.float32


def test_smoothing_runs_on_the_z_scored_windows(neural, behavior, output_dir):
    """The baseline statistics come from the unsmoothed window, so a response
    smoothed into the baseline frames does not change them."""
    from scipy.ndimage import gaussian_filter1d

    info = ETA(
        neural,
        behavior,
        output_dir,
        params={**BASELINE_PARAMS, "smooth_sigma_frames": 1.5},
    )

    z = _baseline_z(_windows(neural.data))
    smoothed = gaussian_filter1d(z, 1.5, axis=1, mode="constant") / gaussian_filter1d(
        np.ones_like(z), 1.5, axis=1, mode="constant"
    )
    np.testing.assert_allclose(info["mean"].data, smoothed.mean(axis=0).T)
    assert (
        info["mean_heatmap"].meta.title
        == "n = 3 events, per-event z-score, smoothed 1.5 frames"
    )


def test_a_constant_baseline_is_skipped_with_smoothing_on(
    neural, behavior, output_dir, caplog
):
    caplog.set_level(logging.WARNING, logger="optinist")
    x = neural.data.copy()
    x[STARTS[0] - 3 : STARTS[0], 2] = 0.5  # flat right before the response

    ETA(
        FluoData(x, file_name="f"),
        behavior,
        output_dir,
        params={**BASELINE_PARAMS, "smooth_sigma_frames": 1.5},
    )

    assert "1 event-cell pairs skipped" in caplog.text


def test_negative_smoothing_fails_with_a_clear_message(neural, behavior, output_dir):
    with pytest.raises(ValueError, match="smooth_sigma_frames -1.0 must be >= 0"):
        ETA(neural, behavior, output_dir, params={**PARAMS, "smooth_sigma_frames": -1})


def test_titles_carry_the_event_and_cell_counts(neural, behavior, output_dir):
    info = ETA(neural, behavior, output_dir, params=dict(PARAMS))
    assert info["mean_heatmap"].meta.title == "n = 3 events"
    assert info["mean_trace"].meta.title == "n = 3 events, 3 cells"

    z = ETA(neural, behavior, output_dir, params=dict(BASELINE_PARAMS))
    assert z["mean_heatmap"].meta.title == "n = 3 events, per-event z-score"


def test_mean_trace_title_counts_only_the_cells_it_averages(neural, output_dir):
    x = neural.data.copy()
    x[:, 1] = 0.0  # a silent cell: every baseline is constant, so it is NaN

    info = _eta(x, output_dir)

    assert np.isnan(info["mean"].data[1]).all()
    assert info["mean_trace"].meta.title == "n = 3 events, per-event z-score, 2 cells"
