"""A 1D HDF5/Mat dataset loads as IscellData (no time-series reshape), so wiring
it into a BehaviorData port used to die on `Y[:, col]` at run time. The Runner
now casts such inputs to a (time, 1) BehaviorData column; everything already
working (behavior CSV, the fluo_from_hdf5 hop, iscell ports) stays untouched.
"""
import h5py
import numpy as np
import pytest

from studio.app.common.core.rules.file_writer import FileWriter
from studio.app.common.core.rules.runner import Runner, _cast_behavior_inputs
from studio.app.common.core.snakemake.smk import Rule
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.data_utils.fluo_from_hdf5 import fluo_from_hdf5
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.eta import ETA

N_TIME = 50

ETA_PARAMS = {
    "transpose_x": True,
    "transpose_y": False,
    "event_col_index": 0,
    "trigger_type": "up",
    "trigger_threshold": 0.5,
    "pre_event": -2,
    "post_event": 2,
}


def _square_wave():
    wave = np.zeros(N_TIME)
    wave[10:15] = 1.0
    wave[30:35] = 1.0
    return wave


def test_1d_iscell_at_behavior_port_becomes_time_column():
    behavior = IscellData(_square_wave())
    neural = FluoData(np.zeros((3, N_TIME)))
    info = {"behaviors_data": behavior, "neural_data": neural}

    _cast_behavior_inputs(ETA, info)

    cast = info["behaviors_data"]
    assert isinstance(cast, BehaviorData)
    assert cast.data.shape == (N_TIME, 1)
    assert np.array_equal(cast.data[:, 0], _square_wave())
    assert not np.shares_memory(cast.data, behavior.data)
    assert info["neural_data"] is neural


def test_iscell_port_is_not_touched():
    iscell = IscellData(np.ones(5))
    info = {"iscell": iscell}

    _cast_behavior_inputs(ETA, info)

    assert info["iscell"] is iscell
    assert info["iscell"].data.ndim == 1


@pytest.mark.parametrize(
    "value",
    [
        FluoData(np.zeros((3, N_TIME))),
        BehaviorData(np.zeros((N_TIME, 2))),
        IscellData(np.zeros((3, 4))),
        IscellData(np.array([])),
        IscellData(np.array([(0.0, 1.0)], dtype=[("time", "f8"), ("lick", "f8")])),
        IscellData(np.array([b"a", b"b"])),
    ],
)
def test_already_working_or_degenerate_behavior_inputs_pass_through(value):
    info = {"behaviors_data": value}
    _cast_behavior_inputs(ETA, info)
    assert info["behaviors_data"] is value


def test_unwired_behavior_port_is_skipped():
    info = {"neural_data": FluoData(np.zeros((3, N_TIME)))}
    _cast_behavior_inputs(ETA, info)
    assert set(info) == {"neural_data"}


def test_1d_at_fluo_port_keeps_the_fluo_from_hdf5_semantics(tmp_path):
    iscell = IscellData(_square_wave())
    info = {"fluo": iscell}

    _cast_behavior_inputs(fluo_from_hdf5, info)

    assert info["fluo"] is iscell
    out = fluo_from_hdf5(iscell, str(tmp_path), params={"transpose": True})
    assert out["fluorescence"].data.shape == (1, N_TIME)


def _load_1d_from_hdf5(tmp_path):
    h5 = tmp_path / "in.h5"
    with h5py.File(h5, "w") as f:
        f["g/behavior"] = _square_wave()
    output = tmp_path / "output" / "default" / "uid" / "input_x" / "input_x.pkl"
    output.parent.mkdir(parents=True, exist_ok=True)
    rule = Rule(
        input=str(h5),
        return_arg="input_x",
        params={},
        output=str(output),
        type="hdf5",
        nwbfile={"image_series": {}},
        hdf5Path="g/behavior",
    )
    return FileWriter.hdf5(rule)["input_x"]


def _eta_inputs(tmp_path):
    loaded = _load_1d_from_hdf5(tmp_path)
    assert isinstance(loaded, IscellData)
    return {
        "behaviors_data": loaded,
        "neural_data": FluoData(np.random.default_rng(0).random((3, N_TIME))),
    }


def test_eta_runs_on_a_1d_hdf5_behavior_dataset(tmp_path):
    info = _eta_inputs(tmp_path)

    _cast_behavior_inputs(ETA, info)
    result = ETA(**info, output_dir="w1/u1/eta-1", params=dict(ETA_PARAMS))

    assert result["mean"].data.shape[0] == 3


def test_runner_execute_function_applies_the_cast(tmp_path):
    info = _eta_inputs(tmp_path)

    result = Runner._Runner__execute_function(
        path="optinist/basic_neural_analysis/eta",
        params=dict(ETA_PARAMS),
        nwb_params=None,
        output_dir="w1/u1/eta-1",
        input_info=info,
    )

    assert isinstance(info["behaviors_data"], BehaviorData)
    assert result["mean"].data.shape[0] == 3


def test_eta_col_index_1_on_cast_column_fails_with_out_of_bounds(tmp_path):
    info = _eta_inputs(tmp_path)
    _cast_behavior_inputs(ETA, info)

    params = dict(ETA_PARAMS, event_col_index=1)
    with pytest.raises(IndexError, match="out of bounds"):
        ETA(**info, output_dir="w1/u1/eta-1", params=params)
