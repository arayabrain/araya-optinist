"""Every ROI-detection node keeps FluoData as (roi, time) but must write the NWB
RoiResponseSeries as (time, roi). LCCD, vacant_roi and spike_deconv used to write
(roi, time), so `fluo_from_hdf5` (transpose: True) reversed their axes on reload.
"""
import inspect
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import yaml

from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import FluoData, Suite2pData
from studio.app.optinist.wrappers.data_utils.vacant_roi import vacant_roi
from studio.app.optinist.wrappers.lccd import lccd_detection
from studio.app.optinist.wrappers.lccd.lccd_detection import lccd_detect
from studio.app.optinist.wrappers.optinist.neural_population_analysis.correlation import (  # noqa: E501
    correlation,
)
from studio.app.optinist.wrappers.suite2p.spike_deconv import suite2p_spike_deconv

N_ROI, N_TIME, H, W = 3, 40, 8, 8


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def images(tmp_path):
    frames = np.random.default_rng(0).random((N_TIME, H, W)).astype(np.float32)
    return ImageData(frames, output_dir=str(tmp_path), file_name="img")


def _series(info):
    (series,) = info["nwbfile"][NWBDATASET.FLUORESCENCE].values()
    return series


def _assert_nwb_is_time_first(info, fluo_key, n_roi):
    fluo = info[fluo_key].data
    for s in _series(info).values():
        assert s["data"].shape == (N_TIME, n_roi)
        assert len(s["region"]) == n_roi
        if n_roi:
            assert fluo.shape == (n_roi, N_TIME)
            assert np.array_equal(s["data"], fluo.T)


def test_vacant_roi_writes_time_first(images, output_dir):
    info = vacant_roi(images, output_dir, params={"f0_frames": 5, "f0_percentile": 8})
    _assert_nwb_is_time_first(info, "fluorescence", n_roi=0)


def test_lccd_writes_time_first(images, output_dir, monkeypatch):
    class FakeLCCD:
        def __init__(self, params):
            pass

        def apply(self, D):
            roi = np.zeros((D.shape[0] * D.shape[1], N_ROI))
            for i in range(N_ROI):
                roi[i * 4 : (i + 1) * 4, i] = 1
            return roi

    fake = types.ModuleType("lccd")
    fake.LCCD = FakeLCCD
    monkeypatch.setitem(
        sys.modules, f"{lccd_detection.__package__}.lccd_python.lccd", fake
    )

    info = lccd_detect(images, output_dir, params={"f0_frames": 5, "f0_percentile": 8})
    _assert_nwb_is_time_first(info, "fluorescence", n_roi=N_ROI)


def test_spike_deconv_writes_time_first(output_dir, monkeypatch):
    F = np.random.default_rng(1).random((N_ROI, N_TIME))
    fake = types.ModuleType("suite2p")
    fake.default_ops = lambda: {
        "neucoeff": 0.0,
        "baseline": "maximin",
        "win_baseline": 60.0,
        "sig_baseline": 10.0,
        "prctile_baseline": 8.0,
        "batch_size": 500,
        "tau": 1.0,
    }
    fake.extraction = types.SimpleNamespace(
        preprocess=lambda F, **kw: F, oasis=lambda F, **kw: F
    )
    monkeypatch.setitem(sys.modules, "suite2p", fake)

    ops = Suite2pData({"F": F, "Fneu": np.zeros_like(F), "fs": 30.0, "stat": []})
    info = suite2p_spike_deconv(ops, output_dir, params={})
    _assert_nwb_is_time_first(info, "spks", n_roi=N_ROI)


def test_correlation_default_correlates_rois_not_timepoints(output_dir):
    params_path = Path(inspect.getfile(correlation)).parent / "params/correlation.yaml"
    with open(params_path) as f:
        params = yaml.safe_load(f)

    fluo = FluoData(np.random.default_rng(2).random((N_ROI, N_TIME)), file_name="f")
    info = correlation(fluo, output_dir, params=params)

    assert info["corr"].data.shape == (N_ROI, N_ROI)
