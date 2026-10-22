"""The ROI nodes' NWB files: pixel masks in the ROI table, the all-ROI label
image as all_roi_img, and both valid NWB."""
import sys
import types

import numpy as np
import pytest
import scipy.sparse as sp
from pynwb import NWBHDF5IO, validate

from studio.app.common.core.rules.file_writer import dataclass_for_rank
from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.dataclass import ImageData
from studio.app.dir_path import CORE_PARAM_PATH
from studio.app.optinist.core.edit_ROI.wrappers.caiman_edit_roi.utils import set_nwbfile
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.core.nwb.nwb_creater import save_nwb
from studio.app.optinist.dataclass import EditRoiData
from studio.app.optinist.dataclass.roi import nwb_all_roi_img
from studio.app.optinist.wrappers.caiman import cnmf
from studio.app.optinist.wrappers.data_utils.vacant_roi import vacant_roi
from studio.app.optinist.wrappers.lccd import lccd_detection
from studio.app.optinist.wrappers.lccd.lccd_detection import lccd_detect

N_ROI, N_TIME, H, W = 3, 20, 8, 8
LCCD_PARAMS = {"f0_frames": 5, "f0_percentile": 8}
# what the ROI table and all_roi_img are written as
CHECKED = ("PlaneSegmentation", "pixel_mask", "image_mask", "PostProcess", "all_roi")


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def images(tmp_path):
    frames = np.random.default_rng(0).random((N_TIME, H, W)).astype(np.float32)
    return ImageData(frames, output_dir=str(tmp_path), file_name="img")


@pytest.fixture
def lccd_stub(monkeypatch):
    class FakeLCCD:
        def __init__(self, params):
            pass

        def apply(self, D):
            roi = np.zeros((D.shape[0] * D.shape[1], N_ROI))
            for i in range(N_ROI):
                roi[i * 5 : i * 5 + 7, i] = 1  # overlapping neighbours
            return roi

    fake = types.ModuleType("lccd")
    fake.LCCD = FakeLCCD
    monkeypatch.setitem(
        sys.modules, f"{lccd_detection.__package__}.lccd_python.lccd", fake
    )


def write_and_read(tmp_path, images, nwb_config):
    """Write nwb_config as save_nwb does, validate it, and read back each
    ROI's pixel mask and the all_roi_img of its one node."""
    input_config = ConfigReader.read(CORE_PARAM_PATH.nwb.value)
    input_config["image_series"]["external_file"] = images
    path = str(tmp_path / "func.nwb")
    save_nwb(path, input_config, nwb_config)

    errors, status = validate(paths=[path])
    assert status == 0
    assert [str(e) for e in errors if any(k in str(e) for k in CHECKED)] == []

    (function_id,) = nwb_config[NWBDATASET.ROI]
    with NWBHDF5IO(path, "r") as io:
        nwbfile = io.read()
        table = nwbfile.processing["ophys"]["ImageSegmentation"][function_id]
        assert "image_mask" not in table.colnames
        masks = [
            np.array([tuple(p) for p in table["pixel_mask"][i]], float).reshape(-1, 3)
            for i in range(len(table))
        ]
        container = nwbfile.processing["optinist"][f"{function_id}_all_roi_img"]
        all_roi_img = container.data[:]
    # an HDF5 input of this dataset reaches roi_from_hdf5 as ImageData
    assert dataclass_for_rank(all_roi_img.ndim) is ImageData
    return masks, all_roi_img


def pixel_rows(mask, weight=1.0):
    rows, cols = np.nonzero(mask)
    return np.column_stack([rows, cols, np.full(rows.size, weight)])


def test_lccd_writes_pixel_masks_and_the_label_image(
    tmp_path, images, output_dir, lccd_stub
):
    info = lccd_detect(images, output_dir, params=LCCD_PARAMS)

    masks, all_roi_img = write_and_read(tmp_path, images, info["nwbfile"])

    roi_masks = info["edit_roi_data"].roi_masks
    assert len(masks) == N_ROI
    for i, mask in enumerate(masks):
        layer = ~np.isnan(roi_masks.label_image([i]))
        np.testing.assert_array_equal(mask, pixel_rows(layer))
    np.testing.assert_array_equal(all_roi_img, info["all_roi"].data[np.newaxis])


def test_vacant_roi_writes_an_empty_table_and_an_empty_label_image(
    tmp_path, images, output_dir
):
    info = vacant_roi(images, output_dir, params=LCCD_PARAMS)

    masks, all_roi_img = write_and_read(tmp_path, images, info["nwbfile"])

    assert masks == []
    assert all_roi_img.shape == (1, H, W) and np.isnan(all_roi_img).all()


def caiman_config(function_id, roi_masks, roi_list, iscell, fluorescence):
    return {
        NWBDATASET.ROI: {function_id: {"roi_list": roi_list}},
        NWBDATASET.POSTPROCESS: {
            function_id: {"all_roi_img": nwb_all_roi_img(roi_masks.label_image())}
        },
        NWBDATASET.COLUMN: {
            function_id: {"name": "iscell", "description": "iscell", "data": iscell}
        },
        NWBDATASET.FLUORESCENCE: {
            function_id: {
                "Fluorescence": {
                    "table_name": "ROIs",
                    "region": list(range(len(fluorescence))),
                    "name": "Fluorescence",
                    "data": fluorescence,
                    "unit": "lumens",
                    "rate": 30.0,
                }
            }
        },
    }


def test_caiman_writes_its_weights_as_pixel_masks(tmp_path, images, monkeypatch):
    # get_roi's footprint: the component's non-zero pixels, read in Fortran order
    monkeypatch.setattr(
        cnmf,
        "get_roi",
        lambda A, thr, method, swap, dims: (
            A[:, [k]].toarray().reshape(dims, order="F") > 0 for k in range(A.shape[1])
        ),
    )
    rng = np.random.default_rng(2)
    A = np.zeros((H * W, 3))
    A[5:14, 0] = rng.random(9) + 0.1
    A[30:36, 2] = rng.random(6) + 0.1  # component 1 is empty
    C = rng.random((3, N_TIME))

    roi_masks, iscell, roi_list, F, *_ = cnmf.component_outputs(
        sp.csc_matrix(A), C, [2, 1], [0], (H, W), 0.9, "nrg", False, n_frames=N_TIME
    )
    config = caiman_config("caiman_cnmf_x", roi_masks, roi_list, iscell, F)
    masks, all_roi_img = write_and_read(tmp_path, images, config)

    for row, comp in enumerate([2, 1, 0]):
        weights = A[:, comp].reshape((H, W), order="F")
        np.testing.assert_array_equal(masks[row][:, :2], pixel_rows(weights)[:, :2])
        np.testing.assert_allclose(masks[row][:, 2], weights[weights != 0])
    assert len(masks[1]) == 0
    np.testing.assert_array_equal(all_roi_img[0], roi_masks.label_image())


def test_an_roi_edit_commit_writes_binary_pixel_masks(tmp_path, images):
    stack = np.full((2, H, W), np.nan)
    stack[0, 1:3, 1:4] = 0
    stack[1, 5, 5:8] = 1
    data = EditRoiData(images=None, roi_masks=stack)

    config = set_nwbfile(
        data, np.array([1, 0]), "caiman_cnmf_x", 30.0, np.ones((2, N_TIME))
    )
    masks, all_roi_img = write_and_read(tmp_path, images, config)

    for i in range(2):
        np.testing.assert_array_equal(masks[i], pixel_rows(~np.isnan(stack[i])))
    np.testing.assert_array_equal(
        all_roi_img, np.nanmax(stack, axis=0, initial=np.nan)[np.newaxis]
    )
