"""HDF5/NWB datasets entering a workflow: dataclass by rank, tiff under the
node's own output dir, and NWB fluorescence oriented to (roi, time) using the
sibling `rois` dataset so the transpose knob on fluo_from_hdf5 is no longer
load-bearing for NWB input.
"""
import os

import h5py
import numpy as np
import pytest

from studio.app.common.core.rules.file_writer import FileWriter
from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.dataclass import ImageData
from studio.app.dir_path import DIRPATH
from studio.app.optinist.dataclass import FluoData, IscellData
from studio.app.optinist.wrappers.data_utils.data_slice import data_slice
from studio.app.optinist.wrappers.data_utils.data_transpose import data_transpose
from studio.app.optinist.wrappers.data_utils.fluo_from_hdf5 import fluo_from_hdf5

N_ROI, N_TIME = 7, 50
LEGACY_NWB = f"{DIRPATH.INPUT_DIR}/1/files/test.nwb"


def _rule(tmp_path, h5_path, dataset):
    output = tmp_path / "output" / "default" / "uid" / "input_x" / "input_x.pkl"
    output.parent.mkdir(parents=True)
    return Rule(
        input=str(h5_path),
        return_arg="input_x",
        params={},
        output=str(output),
        type="hdf5",
        nwbfile={"image_series": {}},
        hdf5Path=dataset,
    )


def _h5(tmp_path, datasets, rois=None):
    path = tmp_path / "in.h5"
    with h5py.File(path, "w") as f:
        for name, arr in datasets.items():
            f[name] = arr
        if rois is not None:
            f["g/rois"] = np.arange(rois)
    return path


def test_3d_dataset_becomes_image_under_the_node_output_dir(tmp_path):
    h5 = _h5(tmp_path, {"g/data": np.zeros((5, 4, 4), dtype=np.float64)})
    rule = _rule(tmp_path, h5, "g/data")

    image = FileWriter.hdf5(rule)["input_x"]

    assert isinstance(image, ImageData)
    assert image.path == [
        os.path.join(os.path.dirname(rule.output), "tiff", "image", "image.tif")
    ]
    assert image.data.dtype == np.float32


def test_2d_dataset_without_rois_is_fluo_and_untouched(tmp_path):
    arr = np.random.default_rng(0).random((N_TIME, N_ROI))
    h5 = _h5(tmp_path, {"g/data": arr})

    fluo = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    assert isinstance(fluo, FluoData)
    assert np.array_equal(fluo.data, arr)
    assert not getattr(fluo, "nwb_oriented", False)


def test_1d_dataset_is_iscell(tmp_path):
    h5 = _h5(tmp_path, {"g/data": np.ones(N_ROI)})
    assert isinstance(
        FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"], IscellData
    )


def test_nwb_time_first_fluorescence_is_oriented_to_roi_time(tmp_path):
    arr = np.random.default_rng(1).random((N_TIME, N_ROI))
    h5 = _h5(tmp_path, {"g/data": arr}, rois=N_ROI)

    fluo = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    assert fluo.data.shape == (N_ROI, N_TIME)
    assert np.array_equal(fluo.data, arr.T)
    assert len(fluo.index) == N_TIME
    assert fluo.nwb_oriented


def test_legacy_roi_first_fluorescence_is_left_alone(tmp_path):
    arr = np.random.default_rng(2).random((N_ROI, N_TIME))
    h5 = _h5(tmp_path, {"g/data": arr}, rois=N_ROI)

    fluo = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    assert np.array_equal(fluo.data, arr)
    assert fluo.nwb_oriented


@pytest.mark.skipif(not os.path.exists(LEGACY_NWB), reason="fixture missing")
def test_repo_legacy_nwb_fixture_comes_out_roi_time(tmp_path):
    rule = _rule(
        tmp_path, LEGACY_NWB, "processing/ophys/Fluorescence/Fluorescence/data"
    )
    fluo = FileWriter.hdf5(rule)["input_x"]
    assert fluo.data.shape == (67, 1000)


def test_fluo_from_hdf5_does_not_undo_the_orientation(tmp_path):
    arr = np.random.default_rng(3).random((N_TIME, N_ROI))
    h5 = _h5(tmp_path, {"g/data": arr}, rois=N_ROI)
    fluo = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    out = fluo_from_hdf5(fluo, str(tmp_path), params={"transpose": True})

    assert out["fluorescence"].data.shape == (N_ROI, N_TIME)


def test_fluo_from_hdf5_still_transposes_plain_arrays(tmp_path):
    fluo = FluoData(np.zeros((N_TIME, N_ROI)))
    out = fluo_from_hdf5(fluo, str(tmp_path), params={"transpose": True})
    assert out["fluorescence"].data.shape == (N_ROI, N_TIME)


def test_hdf5_fluo_passes_through_data_utils(tmp_path):
    h5 = _h5(tmp_path, {"g/data": np.zeros((N_TIME, N_ROI))})
    fluo = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    assert data_transpose(fluo, str(tmp_path), params={})[
        "transposed_data"
    ].data.shape == (
        N_ROI,
        N_TIME,
    )
    assert "sliced_data" in data_slice(fluo, str(tmp_path), params={"slice_dims": None})
