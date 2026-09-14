import importlib.util
import inspect
from pathlib import Path

import numpy as np
import pytest
import yaml

from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData

mod = importlib.import_module(
    "studio.app.optinist.wrappers.optinist.dimension_reduction.dpca_fit"
)

REPO_ROOT = Path(__file__).resolve().parents[5]
SAMPLE_CSV = REPO_ROOT / "sample_data/tutorial/input/sample_mouse2p_behavior.csv"
N_ROI, N_TIME = 30, 2000


@pytest.fixture(autouse=True)
def no_sklearn_needed(monkeypatch):
    if importlib.util.find_spec("sklearn") is None:
        monkeypatch.setattr(mod, "standard_norm", lambda X, mean, std: X)


@pytest.fixture
def defaults():
    path = Path(inspect.getfile(mod)).parent / "params/dpca.yaml"
    nested = yaml.safe_load(path.read_text())
    flat = {}
    mod.recursive_flatten_params(nested, flat)
    return flat


@pytest.fixture
def behavior():
    return np.loadtxt(SAMPLE_CSV, delimiter=",")


@pytest.fixture
def fluo():
    return np.random.default_rng(0).random((N_ROI, N_TIME))


def _prepare(fluo, behavior, defaults, **over):
    params = {**defaults, **over}
    return mod.prepare_inputs(fluo, behavior, None, params)


def test_build_trials_groups_all_features_and_keeps_every_trial():
    rng = np.random.default_rng(1)
    D = rng.random((200, 5))
    triggers = np.arange(20, 180, 10)
    f1 = np.array([0, 1] * 8)
    f2 = np.array([0, 0, 1, 1] * 4)
    f3 = np.array([0] * 12 + [1] * 4)
    trialX, levels = mod.build_trials(D, triggers, [f1, f2, f3], [0, 1, 2], [-2, 2])

    assert trialX.shape == (3, 5, 4, 2, 2, 2)
    assert [list(u) for u in levels] == [[0, 1], [0, 1], [0, 1]]
    present = ~np.isnan(trialX[:, 0, 0])
    assert present.sum(axis=0).tolist() == [[[3, 1], [3, 1]], [[3, 1], [3, 1]]]
    trial = trialX[0, :, :, 0, 0, 0]
    assert np.array_equal(trial, D[triggers[0] - 2 : triggers[0] + 2].T)
    assert not np.isnan(np.nanmean(trialX, axis=0)).any()


def test_build_trials_drops_windows_outside_recording():
    D = np.random.default_rng(2).random((100, 3))
    triggers = np.array([2, 50, 60, 99])
    f = np.array([0, 1, 0, 1])
    trialX, _ = mod.build_trials(D, triggers, [f], [0], [-5, 5])
    assert trialX.shape[0] == 1
    with pytest.raises(ValueError, match="no trigger window"):
        mod.build_trials(D, np.array([2, 99]), [np.array([0, 1])], [0], [-5, 5])


def test_build_trials_rejects_missing_combination_and_bad_features():
    D = np.zeros((100, 2))
    triggers = np.arange(10, 90, 10)
    with pytest.raises(ValueError, match=r"no trials for .*\(1, 1\)"):
        mod.build_trials(
            D,
            triggers,
            [np.array([0, 0, 0, 0, 1, 1, 1, 1]), np.array([0, 1, 0, 1, 0, 0, 0, 0])],
            [0, 2],
            [-1, 1],
        )
    with pytest.raises(ValueError, match="single value"):
        mod.build_trials(D, triggers, [np.zeros(8)], [4], [-1, 1])
    with pytest.raises(ValueError, match="continuous"):
        mod.build_trials(D, np.arange(2, 98, 4), [np.arange(24) / 23], [3], [-1, 1])


def test_calc_trigger_counts_signal_already_high_at_frame_zero():
    signal = np.array([1, 1, 0, 0, 1, 0])
    assert mod.calc_trigger(signal, "up", 0.5).tolist() == [0, 4]
    assert mod.calc_trigger(signal, "cross", 0.5).tolist() == [0, 2, 4, 5]
    assert mod.calc_trigger(signal, "down", 0.5).tolist() == [2, 5]


def test_defaults_run_on_sample_behavior(fluo, behavior, defaults):
    prepared = _prepare(fluo, behavior, defaults)
    assert prepared["trialX"].shape[1:] == (N_ROI, 20, 2, 2)
    assert prepared["labels"] == "tbc"
    assert prepared["figure_features"] == ["t", "b", "c", "tbc"]


@pytest.mark.parametrize(
    "over, match",
    [
        ({"transpose": False}, "share the time axis"),
        ({"feature_columns": [3, 9]}, "out of range"),
        ({"trigger_duration": [10, -10]}, "before < after"),
        ({"labels": "tb"}, "labels must be 3"),
        ({"figure_features": ["t", "x"]}, "not marginalizations"),
        ({"n_components": 40}, "exceeds the number of cells"),
        ({"figure_components": [0, 9]}, "figure_components"),
        ({"trigger_threshold": 5}, "no 'up' trigger"),
        ({"feature_columns": [], "labels": "t"}, "at least one behavior column"),
        ({"feature_columns": [0, 7]}, r"no trials for .*\(1.0, -1.0\)"),
        ({"feature_columns": [3, 7]}, "continuous"),
    ],
)
def test_guards_raise_before_fitting(fluo, behavior, defaults, over, match):
    with pytest.raises(ValueError, match=match):
        _prepare(fluo, behavior, defaults, **over)


def test_string_zero_from_old_workflow_is_coerced(fluo, behavior, defaults):
    prepared = _prepare(fluo, behavior, defaults, figure_components=["0", 1])
    assert prepared["figure_components"] == [0, 1]


@pytest.mark.skipif(
    importlib.util.find_spec("dPCA") is None, reason="dPCA only in the conda env"
)
def test_dpca_fit_end_to_end(fluo, behavior, defaults):
    info = mod.dpca_fit(
        FluoData(fluo), BehaviorData(behavior), "default/uid/dpca_x", params=defaults
    )
    heatmaps = [k for k in info if k != "nwbfile"]
    assert heatmaps == [
        f"{f}-component{c}" for f in ["t", "b", "c", "tbc"] for c in [0, 1]
    ]
    assert info["t-component0"].data.shape == (4, 20)
    assert list(info["t-component0"].columns) == list(range(-10, 10))
    post = info["nwbfile"][NWBDATASET.POSTPROCESS]["dpca_x"]
    assert post["explained_variance_ratio_t"].shape == (8,)
    assert post["levels_c"].tolist() == [0.0, 1.0]
