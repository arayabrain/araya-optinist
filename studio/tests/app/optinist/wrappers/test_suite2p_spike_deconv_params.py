"""suite2p_spike_deconv lays its yaml over suite2p 1.1.0's settings tree and
reads tau from either a 1.1.0 or a 0.10.3 roi pickle. suite2p is stubbed.
"""
import copy
import sys
import types

import numpy as np
import pytest

from studio.app.common.core.workflow.workflow_params import read_default_params
from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.wrappers.suite2p.spike_deconv import suite2p_spike_deconv
from studio.tests.app.optinist.suite2p_settings_1_1_0 import default_settings

N_ROI, FRAMES = 2, 8


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "suite2p_spike_deconv_x"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def suite2p_stub(monkeypatch):
    calls = {}

    def preprocess(F, **kwargs):
        calls["preprocess"] = kwargs
        calls["preprocess_F"] = F.copy()
        return F

    def oasis(F, **kwargs):
        calls["oasis"] = kwargs
        return F + 1

    fake = types.ModuleType("suite2p")
    fake.default_settings = default_settings
    fake.extraction = types.SimpleNamespace(preprocess=preprocess, oasis=oasis)
    monkeypatch.setitem(sys.modules, "suite2p", fake)
    monkeypatch.setitem(
        sys.modules, "torch", types.SimpleNamespace(device=lambda name: name)
    )
    return calls


def _ops(F, **extra):
    pixel = {"ypix": np.array([0]), "xpix": np.array([0]), "lam": np.ones(1)}
    ops = {"F": F, "Fneu": F * 0.2, "fs": 30.0, "stat": [pixel] * len(F)}
    ops.update(extra)
    return Suite2pData(ops)


def test_yaml_and_the_roi_pickles_tau_reach_suite2p(output_dir, suite2p_stub):
    F = np.arange(N_ROI * FRAMES, dtype=np.float32).reshape(N_ROI, FRAMES)
    params = copy.deepcopy(read_default_params("suite2p_spike_deconv"))
    params["dcnv_preprocess"]["baseline"] = "prctile"
    params["extraction"]["neuropil_coefficient"] = 0.5  # not suite2p's default

    info = suite2p_spike_deconv(
        _ops(F, settings={"tau": 1.5}), output_dir, params=params
    )

    # F - neuropil_coefficient * Fneu, with the stub's Fneu = 0.2 F
    np.testing.assert_allclose(suite2p_stub["preprocess_F"], 0.9 * F, rtol=1e-6)
    assert suite2p_stub["preprocess"] == {
        "fs": 30.0,
        "batch_size": params["extraction"]["batch_size"],
        "device": "cpu",
        **params["dcnv_preprocess"],
    }
    assert suite2p_stub["oasis"] == {
        "batch_size": params["extraction"]["batch_size"],
        "tau": 1.5,
        "fs": 30.0,
    }
    np.testing.assert_allclose(info["spks"].data, 0.9 * F + 1, rtol=1e-6)
    # the pickle records what ran, not the roi node's deconvolution defaults
    saved = info["ops"].data["settings"]
    assert saved["tau"] == 1.5
    assert saved["dcnv_preprocess"] == params["dcnv_preprocess"]
    assert saved["extraction"]["neuropil_coefficient"] == 0.5


def test_the_roi_nodes_extraction_settings_are_kept(output_dir, suite2p_stub):
    # F and Fneu were extracted upstream with these; only the two deconvolution
    # keys are this node's
    upstream = {
        "tau": 1.0,
        "extraction": {
            "allow_overlap": True,
            "min_neuropil_pixels": 20,
            "neuropil_coefficient": 0.7,
            "batch_size": 500,
        },
    }
    params = copy.deepcopy(read_default_params("suite2p_spike_deconv"))
    params["extraction"].update(neuropil_coefficient=0.5, batch_size=100)

    info = suite2p_spike_deconv(
        _ops(np.ones((N_ROI, FRAMES), np.float32), settings=upstream),
        output_dir,
        params=params,
    )

    assert info["ops"].data["settings"]["extraction"] == {
        "allow_overlap": True,
        "min_neuropil_pixels": 20,
        "neuropil_coefficient": 0.5,
        "batch_size": 100,
    }


def test_a_pre_upgrade_roi_pickle_stores_tau_flat(output_dir, suite2p_stub):
    info = suite2p_spike_deconv(
        _ops(np.ones((N_ROI, FRAMES), np.float32), tau=0.7),
        output_dir,
        params=read_default_params("suite2p_spike_deconv"),
    )
    assert suite2p_stub["oasis"]["tau"] == 0.7
    assert info["ops"].data["settings"]["tau"] == 0.7


def test_no_rois_gives_no_spikes_without_calling_suite2p(output_dir, suite2p_stub):
    info = suite2p_spike_deconv(
        _ops(np.zeros((0, FRAMES), np.float32)),
        output_dir,
        params=read_default_params("suite2p_spike_deconv"),
    )
    assert info["ops"].data["spks"].shape == (0, FRAMES)
    assert len(info["spks"].data) == 0
    assert "oasis" not in suite2p_stub


@pytest.mark.parametrize(
    "group, key, value, baseline",
    [
        ("extraction", "batch_size", 0, "maximin"),  # no batch runs: zero spikes
        ("extraction", "batch_size", -1, "maximin"),
        ("dcnv_preprocess", "sig_baseline", 0, "maximin"),  # NaN baseline
        ("dcnv_preprocess", "sig_baseline", 0, "constant"),
        ("dcnv_preprocess", "win_baseline", 0, "maximin"),  # 1-frame window
    ],
)
def test_a_setting_that_zeroes_or_nans_the_spikes_is_refused(
    output_dir, suite2p_stub, group, key, value, baseline
):
    params = copy.deepcopy(read_default_params("suite2p_spike_deconv"))
    params["dcnv_preprocess"]["baseline"] = baseline
    params[group][key] = value
    with pytest.raises(ValueError, match=key):
        suite2p_spike_deconv(
            _ops(np.ones((N_ROI, FRAMES), np.float32)), output_dir, params=params
        )
    assert "oasis" not in suite2p_stub


def test_prctile_baseline_does_not_read_the_smoothing_settings(
    output_dir, suite2p_stub
):
    params = copy.deepcopy(read_default_params("suite2p_spike_deconv"))
    params["dcnv_preprocess"].update(baseline="prctile", sig_baseline=0)
    suite2p_spike_deconv(
        _ops(np.ones((N_ROI, FRAMES), np.float32)), output_dir, params=params
    )
    assert suite2p_stub["preprocess"]["baseline"] == "prctile"


def test_a_baseline_suite2p_would_silently_skip_is_refused(output_dir, suite2p_stub):
    params = copy.deepcopy(read_default_params("suite2p_spike_deconv"))
    params["dcnv_preprocess"]["baseline"] = "constant_prctile"
    with pytest.raises(ValueError, match="constant_prctile"):
        suite2p_spike_deconv(
            _ops(np.ones((N_ROI, FRAMES), np.float32)), output_dir, params=params
        )
