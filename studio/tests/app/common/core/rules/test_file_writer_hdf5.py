"""HDF5/NWB datasets entering a workflow: dataclass by rank, tiff cached per
workspace, and NWB fluorescence oriented to (roi, time) using the
sibling `rois` dataset so the transpose knob on fluo_from_hdf5 is no longer
load-bearing for NWB input.
"""
import os
import pickle

import h5py
import numpy as np
import pytest
import scipy.io

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
    output.parent.mkdir(parents=True, exist_ok=True)
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


def test_3d_dataset_becomes_image(tmp_path):
    h5 = _h5(tmp_path, {"g/data": np.zeros((5, 4, 4), dtype=np.float64)})
    rule = _rule(tmp_path, h5, "g/data")

    image = FileWriter.hdf5(rule)["input_x"]

    assert isinstance(image, ImageData)
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


def test_scalar_dataset_is_refused_by_name(tmp_path):
    h5 = _h5(tmp_path, {"g/data": np.float64(3.0)})
    with pytest.raises(ValueError, match="'g/data' is a scalar"):
        FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))


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


def test_square_fluorescence_is_ambiguous_and_left_to_the_transpose_param(tmp_path):
    arr = np.random.default_rng(4).random((N_ROI, N_ROI))
    h5 = _h5(tmp_path, {"g/data": arr}, rois=N_ROI)

    fluo = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    assert np.array_equal(fluo.data, arr)
    assert not getattr(fluo, "nwb_oriented", False)
    out = fluo_from_hdf5(fluo, str(tmp_path), params={"transpose": True})
    assert np.array_equal(out["fluorescence"].data, arr.T)


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


def _matrule(tmp_path, mat_path, variable):
    output = tmp_path / "output" / "default" / "uid" / "input_x" / "input_x.pkl"
    output.parent.mkdir(parents=True, exist_ok=True)
    return Rule(
        input=str(mat_path),
        return_arg="input_x",
        params={},
        output=str(output),
        type="matlab",
        nwbfile={"image_series": {}},
        matPath=variable,
    )


@pytest.mark.parametrize("rois", [np.float64(3.0), None])
def test_a_rois_sibling_that_is_not_a_1d_dataset_is_ignored(tmp_path, rois):
    """A scalar `rois` used to raise TypeError and kill the node."""
    arr = np.random.default_rng(5).random((N_TIME, N_ROI))
    path = tmp_path / "in.h5"
    with h5py.File(path, "w") as f:
        f["g/data"] = arr
        if rois is None:
            f.create_group("g/rois")["x"] = np.arange(N_ROI)
        else:
            f["g/rois"] = rois

    fluo = FileWriter.hdf5(_rule(tmp_path, path, "g/data"))["input_x"]

    assert np.array_equal(fluo.data, arr)
    assert not fluo.nwb_oriented


def test_the_tiff_is_cached_per_workspace_not_per_run(tmp_path):
    h5 = _h5(tmp_path, {"g/data": np.zeros((5, 4, 4), dtype=np.float64)})
    first = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    second_output = tmp_path / "output" / "default" / "uid2" / "input_x" / "input_x.pkl"
    second_output.parent.mkdir(parents=True)
    rule2 = _rule(tmp_path, h5, "g/data")
    rule2.output = str(second_output)
    second = FileWriter.hdf5(rule2)["input_x"]

    workspace_dir = tmp_path / "output" / "default"
    assert first.path == second.path
    assert first.path[0].startswith(str(workspace_dir / "input_tiff"))
    assert first.path[0].endswith(os.path.join("tiff", "image", "image.tif"))
    assert os.path.isfile(first.path[0])
    assert not os.path.exists(second_output.parent / "tiff")
    # a different dataset in the same file gets its own entry
    other = _h5(tmp_path, {"g/data": np.ones((5, 4, 4)), "g/two": np.ones((5, 4, 4))})
    assert (
        FileWriter.hdf5(_rule(tmp_path, other, "g/two"))["input_x"].path != first.path
    )


def test_4d_dataset_is_image_and_survives_the_tiff_round_trip(tmp_path):
    arr = np.arange(3 * 2 * 4 * 4, dtype=np.float64).reshape(3, 2, 4, 4)
    h5 = _h5(tmp_path, {"g/data": arr})

    image = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    assert isinstance(image, ImageData)
    assert image.data.shape == arr.shape
    assert np.array_equal(image.data, arr.astype(np.float32))


def test_a_realistically_sized_movie_halves_on_disk_and_reads_back(tmp_path):
    """The only test above toy size: 200 x 256 x 256 float64 in, float32 out."""
    frames, height, width = 200, 256, 256
    h5 = tmp_path / "big.h5"
    with h5py.File(h5, "w") as f:
        f.create_dataset("g/data", shape=(frames, height, width), dtype=np.float64)
    source_bytes = frames * height * width * 8

    image = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    written = os.path.getsize(image.path[0])
    assert written == pytest.approx(source_bytes / 2, rel=0.01)
    assert image.data.shape == (frames, height, width)


def test_nwb_oriented_survives_the_pickle_hop_between_nodes(tmp_path):
    arr = np.random.default_rng(6).random((N_TIME, N_ROI))
    h5 = _h5(tmp_path, {"g/data": arr}, rois=N_ROI)
    fluo = FileWriter.hdf5(_rule(tmp_path, h5, "g/data"))["input_x"]

    revived = pickle.loads(pickle.dumps(fluo))

    assert revived.nwb_oriented
    out = fluo_from_hdf5(revived, str(tmp_path), params={"transpose": True})
    assert out["fluorescence"].data.shape == (N_ROI, N_TIME)


def test_matlab_variables_get_the_same_types_and_tiff_cache(tmp_path):
    mat = tmp_path / "in.mat"
    scipy.io.savemat(
        mat,
        {
            "movie": np.zeros((5, 4, 4), dtype=np.float64),
            "fluo": np.zeros((N_TIME, N_ROI)),
            "iscell": np.ones(N_ROI),
        },
    )

    image = FileWriter.mat(_matrule(tmp_path, mat, "movie"))["input_x"]
    assert isinstance(image, ImageData)
    assert image.data.dtype == np.float32
    assert str(tmp_path / "output" / "default" / "input_tiff") in image.path[0]
    assert isinstance(
        FileWriter.mat(_matrule(tmp_path, mat, "fluo"))["input_x"], FluoData
    )
    assert isinstance(
        FileWriter.mat(_matrule(tmp_path, mat, "iscell"))["input_x"], IscellData
    )
