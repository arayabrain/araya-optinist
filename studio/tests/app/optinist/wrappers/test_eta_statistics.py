"""ETA and correlation statistics: the n next to mean and sem is the event
count, sem is a sample statistic, and degenerate inputs are reported, not NaN."""

import logging
import warnings

import numpy as np
import pytest

from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.eta import ETA
from studio.app.optinist.wrappers.optinist.neural_population_analysis.correlation import (  # noqa: E501
    correlation,
)

NUM_CELL = 3
NUM_FRAME = 200
EVENT_LEN = 5
EVENT_STARTS = [40, 80, 120, 160]  # 4 events, so cells != events
PARAMS = {
    "transpose_x": True,
    "transpose_y": False,
    "event_col_index": 0,
    "trigger_type": "up",
    "trigger_threshold": 0.5,
    "pre_event": -10,
    "post_event": 10,
}


def _behavior(starts=EVENT_STARTS, lengths=None):
    behavior = np.zeros((NUM_FRAME, 1))
    for i, start in enumerate(starts):
        length = EVENT_LEN if lengths is None else lengths[i]
        behavior[start : start + length, 0] = 1.0
    return behavior


def _eta(tmp_path, fluo=None, behavior=None, params=None, iscell=None):
    rng = np.random.default_rng(0)
    fluo = rng.random((NUM_CELL, NUM_FRAME)) if fluo is None else fluo
    return ETA(
        FluoData(fluo, file_name="f"),
        BehaviorData(_behavior() if behavior is None else behavior, file_name="b"),
        str(tmp_path / "default" / "uid" / "eta_1"),
        iscell=iscell,
        params=dict(PARAMS if params is None else params),
    )


def _postprocess(info):
    return next(iter(info["nwbfile"][NWBDATASET.POSTPROCESS].values()))


def test_num_sample_is_the_number_of_averaged_events(tmp_path):
    out = _postprocess(_eta(tmp_path))

    assert out["num_sample"] == [len(EVENT_STARTS)]
    assert out["mean"].shape[0] == NUM_CELL  # and not the cell count


def test_sem_is_sample_std_over_sqrt_n(tmp_path):
    out = _postprocess(_eta(tmp_path))
    n = len(EVENT_STARTS)

    np.testing.assert_allclose(out["sem"], out["std"] / np.sqrt(n))
    fluo = np.random.default_rng(0).random((NUM_CELL, NUM_FRAME))
    windows = np.stack([fluo[:, s - 10 : s + 15] for s in EVENT_STARTS])
    np.testing.assert_allclose(out["std"], np.std(windows, axis=0, ddof=1))


def test_single_event_has_undefined_not_zero_std(tmp_path):
    out = _postprocess(_eta(tmp_path, behavior=_behavior(starts=[80])))

    assert out["num_sample"] == [1]
    assert np.isnan(out["std"]).all()
    assert np.isnan(out["sem"]).all()


def test_flat_cell_normalises_to_a_zero_row_not_nan(tmp_path):
    fluo = np.random.default_rng(0).random((NUM_CELL, NUM_FRAME))
    fluo[1] = 0.7
    heatmap = _eta(tmp_path, fluo=fluo)["mean_heatmap"]

    assert not np.isnan(heatmap.data).any()
    assert (heatmap.data[1] == 0).all()
    assert heatmap.data[0].min() == 0 and heatmap.data[0].max() == 1


def test_positive_pre_event_means_the_same_window_as_negative(tmp_path):
    negative = _eta(tmp_path)
    positive = _eta(tmp_path, params={**PARAMS, "pre_event": 10})

    np.testing.assert_array_equal(negative["mean"].data, positive["mean"].data)
    assert list(positive["mean"].index) == list(range(-10, 15))
    assert positive["mean_heatmap"].columns == list(range(-10, 15))


def test_negative_post_event_ends_the_window_inside_the_trigger(tmp_path):
    # post_event counts from the end of the 5-frame trigger: -5 ends at onset
    info = _eta(tmp_path, params={**PARAMS, "post_event": -EVENT_LEN})

    assert list(info["mean"].index) == list(range(-10, 0))
    assert info["mean"].data.shape == (NUM_CELL, 10)
    fluo = np.random.default_rng(0).random((NUM_CELL, NUM_FRAME))
    windows = np.stack([fluo[:, s - 10 : s] for s in EVENT_STARTS])
    np.testing.assert_allclose(info["mean"].data, windows.mean(axis=0))


def test_empty_window_fails_with_the_three_terms(tmp_path):
    with pytest.raises(AssertionError, match="Empty window.*post_event -15"):
        _eta(tmp_path, params={**PARAMS, "post_event": -(10 + EVENT_LEN)})


def test_dropped_events_are_logged_and_excluded_from_n(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    # one event at frame 4 crosses the start, one event is 3 frames long
    behavior = _behavior(starts=[4, 40, 80, 120], lengths=[5, 5, 3, 5])

    out = _postprocess(_eta(tmp_path, behavior=behavior))

    assert out["num_sample"] == [2]
    assert "1 of 4 triggers dropped" in caplog.text
    assert "1 of 3 events dropped" in caplog.text


def test_iscell_does_not_change_the_event_count(tmp_path):
    out = _postprocess(_eta(tmp_path, iscell=IscellData(np.array([1, 0, 1]))))

    assert out["num_sample"] == [len(EVENT_STARTS)]
    assert out["mean"].shape[0] == 2


def test_constant_roi_is_named_without_a_numpy_warning(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="optinist")
    fluo = np.random.default_rng(0).random((4, 50))
    fluo[2] = 1.0

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        info = correlation(
            FluoData(fluo, file_name="f"),
            str(tmp_path / "default" / "uid" / "corr_1"),
            iscell=IscellData(np.array([1, 1, 1, 0])),
            params={"transpose": False},
        )

    corr = info["corr"].data
    assert np.isnan(corr[2]).all() and np.isnan(corr[:, 2]).all()
    assert np.isfinite(corr[0, 1])
    assert "1 ROI(s) have a constant trace" in caplog.text
    assert "[2]" in caplog.text
