import logging

import numpy as np
import pytest

from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.condition_split import (  # noqa: E501
    condition_split,
)
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.eta import ETA

NUM_CELL = 3
CONDITION = np.array([0.0, 1.0, 2.0, 3.0, np.nan, 2.0, 1.0, 0.0])
PARAMS = {
    "transpose_x": True,
    "transpose_y": False,
    "event_col_index": 1,
    "condition": "greater",
    "threshold": 1.0,
    "threshold_upper": 1.0,
}


def _fluo(num_frame=len(CONDITION)):
    return np.arange(NUM_CELL * num_frame, dtype=float).reshape(NUM_CELL, num_frame)


def _behavior(condition=CONDITION):
    return np.column_stack([np.arange(len(condition)), condition])


def _split(tmp_path, fluo=None, behavior=None, params=None):
    return condition_split(
        FluoData(_fluo() if fluo is None else fluo, file_name="f"),
        BehaviorData(_behavior() if behavior is None else behavior, file_name="b"),
        str(tmp_path / "default" / "uid" / "condition_split_1"),
        params={**PARAMS, **(params or {})},
    )


@pytest.mark.parametrize(
    "condition, threshold, upper, kept",
    [
        ("greater", 1.0, 1.0, [2, 3, 5]),
        ("less", 1.0, 1.0, [0, 7]),
        ("equal", 2.0, 2.0, [2, 5]),
        ("between", 1.0, 2.0, [1, 2, 5, 6]),
    ],
)
def test_condition_keeps_the_matching_samples(
    tmp_path, condition, threshold, upper, kept
):
    out = _split(
        tmp_path,
        params={
            "condition": condition,
            "threshold": threshold,
            "threshold_upper": upper,
        },
    )

    np.testing.assert_array_equal(out["neural_data"].data, _fluo()[:, kept])
    np.testing.assert_array_equal(out["behaviors_data"].data, _behavior()[kept])


def test_outputs_keep_the_input_orientation(tmp_path):
    out = _split(
        tmp_path,
        fluo=_fluo().T,
        behavior=_behavior().T,
        params={"transpose_x": False, "transpose_y": True},
    )

    np.testing.assert_array_equal(out["neural_data"].data, _fluo().T[[2, 3, 5]])
    np.testing.assert_array_equal(
        out["behaviors_data"].data, _behavior().T[:, [2, 3, 5]]
    )


def test_1d_behaviour_is_one_column(tmp_path):
    out = condition_split(
        FluoData(_fluo(), file_name="f"),
        IscellData(CONDITION),
        str(tmp_path / "default" / "uid" / "condition_split_1"),
        params={**PARAMS, "event_col_index": 0},
    )

    np.testing.assert_array_equal(out["neural_data"].data, _fluo()[:, [2, 3, 5]])
    assert out["behaviors_data"].data.shape == (3, 1)


def test_segments_are_logged(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="optinist")

    _split(tmp_path)

    assert "kept 3 of 8 samples in 2 segment(s)" in caplog.text


def test_margin_drops_kept_samples_next_to_rejected_ones(tmp_path):
    condition = np.array([5.0, 5.0, 5.0, 5.0, 0.0, 5.0, 5.0, 5.0, 5.0])
    fluo = _fluo(len(condition))

    for margin, kept in (
        (0, [0, 1, 2, 3, 5, 6, 7, 8]),
        (1, [0, 1, 2, 6, 7, 8]),
        (2, [0, 1, 7, 8]),
    ):
        out = _split(
            tmp_path,
            fluo=fluo,
            behavior=_behavior(condition),
            params={"margin": margin},
        )
        np.testing.assert_array_equal(out["neural_data"].data, fluo[:, kept])


def test_margin_that_rejects_everything_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="and margin 3"):
        _split(tmp_path, params={"margin": 3})


def test_negative_margin_fails(tmp_path):
    with pytest.raises(AssertionError, match="margin must be >= 0"):
        _split(tmp_path, params={"margin": -1})


def test_no_match_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="No sample of behaviour column 1"):
        _split(tmp_path, params={"threshold": 10.0})


def test_between_with_upper_below_threshold_fails(tmp_path):
    with pytest.raises(AssertionError, match="threshold_upper 0.0 must be >="):
        _split(tmp_path, params={"condition": "between", "threshold_upper": 0.0})


def test_unknown_condition_fails(tmp_path):
    with pytest.raises(ValueError, match="condition must be one of"):
        _split(tmp_path, params={"condition": "above"})


def test_out_of_range_column_fails_with_a_clear_message(tmp_path):
    with pytest.raises(AssertionError, match="event_col_index 5 is out of range"):
        _split(tmp_path, params={"event_col_index": 5})


def test_eta_runs_on_the_split_output(tmp_path):
    num_frame = 200
    fluo = np.random.default_rng(0).random((NUM_CELL, num_frame))
    behavior = np.zeros((num_frame, 2))
    behavior[100:, 0] = 1.0
    for start in (20, 50, 120, 150, 180):
        behavior[start : start + 3, 1] = 1.0

    split = _split(
        tmp_path,
        fluo=fluo,
        behavior=behavior,
        params={"event_col_index": 0, "condition": "equal", "threshold": 1.0},
    )
    eta = ETA(
        split["neural_data"],
        split["behaviors_data"],
        str(tmp_path / "default" / "uid" / "eta_1"),
        params={
            "transpose_x": True,
            "transpose_y": False,
            "event_col_index": 1,
            "trigger_type": "up",
            "trigger_threshold": 0.5,
            "pre_event": -5,
            "post_event": 5,
        },
    )

    windows = np.stack([fluo[:, s - 5 : s + 8] for s in (120, 150, 180)])
    np.testing.assert_allclose(eta["mean"].data, windows.mean(axis=0))
