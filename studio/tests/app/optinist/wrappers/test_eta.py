import inspect

import numpy as np
import pytest

from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.eta import ETA

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
