"""The ROI nodes' NWB files: pixel masks in the ROI table, the all-ROI label
image as all_roi_label_img, and both valid NWB."""
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
from studio.app.optinist.core.nwb import nwb_creater
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.core.nwb.nwb_creater import (
    overwrite_nwb,
    overwrite_nwbfile,
    save_nwb,
)
from studio.app.optinist.dataclass import EditRoiData
from studio.app.optinist.dataclass.roi import (
    RoiMasks,
    nwb_roi_label_image,
    nwb_roi_table,
)
from studio.app.optinist.wrappers.caiman import cnmf
from studio.app.optinist.wrappers.data_utils.vacant_roi import vacant_roi
from studio.app.optinist.wrappers.lccd import lccd_detection
from studio.app.optinist.wrappers.lccd.lccd_detection import lccd_detect

N_ROI, N_TIME, H, W = 3, 20, 8, 8
LCCD_PARAMS = {"f0_frames": 5, "f0_percentile": 8}
# what the ROI table and the label image are written as
CHECKED = ("PlaneSegmentation", "pixel_mask", "image_mask", "PostProcess", "all_roi")
# the run's NWB settings with the dense image_mask asked for
SAVE_IMAGE_MASK = {"ophys": {"plane_segmentation": {"save_image_mask": True}}}


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


def write_and_read(tmp_path, images, nwb_config, image_mask=False):
    """Write nwb_config as save_nwb does, validate it, and read back each
    ROI's pixel mask and the label image of its one node; with image_mask, also
    the image_mask column, which is absent otherwise."""
    input_config = ConfigReader.read(CORE_PARAM_PATH.nwb.value)
    input_config["image_series"]["external_file"] = images
    path = str(tmp_path / "func.nwb")
    save_nwb(path, input_config, nwb_config)
    return read_back(path, nwb_config, image_mask)


def read_back(path, nwb_config, image_mask=False):
    """Validate the NWB file at path and read it back as write_and_read does."""
    errors, status = validate(paths=[path])
    assert status == 0
    assert [str(e) for e in errors if any(k in str(e) for k in CHECKED)] == []

    (function_id,) = nwb_config[NWBDATASET.ROI]
    with NWBHDF5IO(path, "r") as io:
        nwbfile = io.read()
        table = nwbfile.processing["ophys"]["ImageSegmentation"][function_id]
        masks = [
            np.array([tuple(p) for p in table["pixel_mask"][i]], float).reshape(-1, 3)
            for i in range(len(table))
        ]
        if image_mask:
            image_masks = table["image_mask"].data[:]
        else:
            assert "image_mask" not in table.colnames
        optinist = nwbfile.processing["optinist"]
        # a new name: code indexing ROIs on the old per-ROI stack fails loudly
        assert f"{function_id}_all_roi_img" not in optinist.data_interfaces
        label_img = optinist[f"{function_id}_all_roi_label_img"].data[:]
    # an HDF5 input of this dataset reaches roi_from_hdf5 as ImageData
    assert dataclass_for_rank(label_img.ndim) is ImageData
    if image_mask:
        return masks, label_img, image_masks
    return masks, label_img


def pixel_rows(mask, weight=1.0):
    rows, cols = np.nonzero(mask)
    return np.column_stack([rows, cols, np.full(rows.size, weight)])


def test_lccd_writes_pixel_masks_and_the_label_image(
    tmp_path, images, output_dir, lccd_stub
):
    info = lccd_detect(images, output_dir, params=LCCD_PARAMS)

    masks, label_img = write_and_read(tmp_path, images, info["nwbfile"])

    roi_masks = info["edit_roi_data"].roi_masks
    assert len(masks) == N_ROI
    for i, mask in enumerate(masks):
        layer = ~np.isnan(roi_masks.label_image([i]))
        np.testing.assert_array_equal(mask, pixel_rows(layer))
    np.testing.assert_array_equal(label_img, info["all_roi"].data[np.newaxis])


def test_vacant_roi_writes_an_empty_table_and_an_empty_label_image(
    tmp_path, images, output_dir
):
    info = vacant_roi(images, output_dir, params=LCCD_PARAMS)

    masks, label_img = write_and_read(tmp_path, images, info["nwbfile"])

    assert masks == []
    assert label_img.shape == (1, H, W) and np.isnan(label_img).all()


def caiman_config(
    function_id, roi_masks, roi_list, iscell, fluorescence, nwb_settings=None
):
    return {
        NWBDATASET.ROI: {
            function_id: nwb_roi_table(roi_list, roi_masks.shape, nwb_settings)
        },
        NWBDATASET.POSTPROCESS: {
            function_id: nwb_roi_label_image(roi_masks.label_image())
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


@pytest.fixture
def caiman_components(monkeypatch):
    """A, and component_outputs' masks, iscell, ROI rows and traces for it: its
    rows in the order accepted 2, 1, then rejected 0; component 1 is empty."""
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
    A[30:36, 2] = rng.random(6) + 0.1
    C = rng.random((3, N_TIME))

    roi_masks, iscell, roi_list, F, *_ = cnmf.component_outputs(
        sp.csc_matrix(A), C, [2, 1], [0], (H, W), 0.9, "nrg", False, n_frames=N_TIME
    )
    return A, roi_masks, iscell, roi_list, F


CAIMAN_ROWS = [2, 1, 0]  # component of each ROI row


def test_caiman_writes_its_weights_as_pixel_masks(tmp_path, images, caiman_components):
    A, roi_masks, iscell, roi_list, F = caiman_components
    config = caiman_config("caiman_cnmf_x", roi_masks, roi_list, iscell, F)
    masks, label_img = write_and_read(tmp_path, images, config)

    for row, comp in enumerate(CAIMAN_ROWS):
        weights = A[:, comp].reshape((H, W), order="F")
        np.testing.assert_array_equal(masks[row][:, :2], pixel_rows(weights)[:, :2])
        np.testing.assert_allclose(masks[row][:, 2], weights[weights != 0])
    assert len(masks[1]) == 0
    np.testing.assert_array_equal(label_img[0], roi_masks.label_image())


def test_the_setting_adds_caimans_dense_image_masks(
    tmp_path, images, caiman_components
):
    A, roi_masks, iscell, roi_list, F = caiman_components
    config = caiman_config(
        "caiman_cnmf_x", roi_masks, roi_list, iscell, F, SAVE_IMAGE_MASK
    )
    masks, _, image_masks = write_and_read(tmp_path, images, config, image_mask=True)

    # the image_mask files written before held: A's column in get_roi's order
    assert image_masks.dtype == np.float32
    for row, comp in enumerate(CAIMAN_ROWS):
        expected = A[:, comp].reshape((H, W), order="F").astype(np.float32)
        np.testing.assert_array_equal(image_masks[row], expected)
    assert len(masks) == len(CAIMAN_ROWS)  # pixel masks are kept beside them


def test_image_masks_are_drawn_a_buffer_at_a_time(tmp_path, images, monkeypatch):
    # more ROIs than the buffer: every frame lands in its row, while no draw
    # holds more than the buffer (a list of every frame doubles the masks' size)
    monkeypatch.setattr(nwb_creater, "IMAGE_MASK_BUFFER", 2)
    drawn = []
    get_data = nwb_creater.ImageMasks._get_data

    def recording(self, selection):
        frames = get_data(self, selection)
        drawn.append(len(frames))
        return frames

    monkeypatch.setattr(nwb_creater.ImageMasks, "_get_data", recording)
    rng = np.random.default_rng(3)
    pixels = [rng.choice(H * W, size=5, replace=False) for _ in range(5)]
    roi_masks = RoiMasks.from_pixels((H, W), pixels)
    roi_list, expected = [], np.zeros((5, H, W), np.float32)
    for i in range(5):
        rows, cols = roi_masks.coords(i)
        weights = rng.random(rows.size) + 0.1
        roi_list.append({"pixel_mask": np.array([rows, cols, weights]).T})
        expected[i, rows, cols] = weights
    config = caiman_config(
        "caiman_cnmf_x",
        roi_masks,
        roi_list,
        np.ones(5, int),
        np.zeros((5, N_TIME)),
        SAVE_IMAGE_MASK,
    )

    _, _, image_masks = write_and_read(tmp_path, images, config, image_mask=True)

    np.testing.assert_array_equal(image_masks, expected)
    assert sum(drawn) == 5 and max(drawn) <= 2


@pytest.mark.parametrize("rewrite", ["whole_nwb", "commit_or_filter"])
def test_image_masks_survive_an_nwb_rewrite(
    tmp_path, images, caiman_components, rewrite
):
    A, roi_masks, iscell, roi_list, F = caiman_components
    config = caiman_config(
        "caiman_cnmf_x", roi_masks, roi_list, iscell, F, SAVE_IMAGE_MASK
    )
    write_and_read(tmp_path, images, config, image_mask=True)
    path = str(tmp_path / "func.nwb")

    if rewrite == "whole_nwb":  # an existing whole.nwb: exported with the new tables
        overwrite_nwbfile(path, config)
    else:  # an ROI-edit commit or the data filter: written anew from its acquisition
        overwrite_nwb(config, str(tmp_path), "func.nwb")
    _, _, image_masks = read_back(path, config, image_mask=True)

    for row, comp in enumerate(CAIMAN_ROWS):
        expected = A[:, comp].reshape((H, W), order="F").astype(np.float32)
        np.testing.assert_array_equal(image_masks[row], expected)


def test_the_setting_adds_lccds_dense_image_masks(
    tmp_path, images, output_dir, lccd_stub
):
    info = lccd_detect(images, output_dir, params=LCCD_PARAMS, nwbfile=SAVE_IMAGE_MASK)

    # the node pickle keeps pixel lists only; the dense masks exist in the file
    (entry,) = info["nwbfile"][NWBDATASET.ROI].values()
    assert all(set(roi) == {"pixel_mask"} for roi in entry["roi_list"])
    masks, _, image_masks = write_and_read(
        tmp_path, images, info["nwbfile"], image_mask=True
    )

    # the image_mask files written before held: LCCD's ROI matrix column
    shape = info["edit_roi_data"].roi_masks.shape
    for i in range(N_ROI):
        column = np.zeros(H * W, dtype=np.float32)
        column[i * 5 : i * 5 + 7] = 1  # FakeLCCD's ROI i
        np.testing.assert_array_equal(image_masks[i], column.reshape(shape))
    assert len(masks) == N_ROI


def test_an_roi_edit_commit_writes_binary_pixel_masks(tmp_path, images):
    stack = np.full((2, H, W), np.nan)
    stack[0, 1:3, 1:4] = 0
    stack[1, 5, 5:8] = 1
    data = EditRoiData(images=None, roi_masks=RoiMasks.from_stack(stack))

    config = set_nwbfile(
        data, np.array([1, 0]), "caiman_cnmf_x", 30.0, np.ones((2, N_TIME))
    )
    masks, label_img = write_and_read(tmp_path, images, config)

    for i in range(2):
        np.testing.assert_array_equal(masks[i], pixel_rows(~np.isnan(stack[i])))
    np.testing.assert_array_equal(
        label_img, np.nanmax(stack, axis=0, initial=np.nan)[np.newaxis]
    )


def test_the_setting_adds_an_roi_edit_commits_binary_image_masks(tmp_path, images):
    stack = np.full((2, H, W), np.nan)
    stack[0, 1:3, 1:4] = 0
    stack[1, 5, 5:8] = 1
    data = EditRoiData(images=None, roi_masks=RoiMasks.from_stack(stack))

    config = set_nwbfile(
        data,
        np.array([1, 0]),
        "caiman_cnmf_x",
        30.0,
        np.ones((2, N_TIME)),
        nwb_settings=SAVE_IMAGE_MASK,
    )
    _, _, image_masks = write_and_read(tmp_path, images, config, image_mask=True)

    np.testing.assert_array_equal(image_masks, ~np.isnan(stack))


def test_the_setting_leaves_vacant_rois_empty_table_valid(tmp_path, images, output_dir):
    info = vacant_roi(images, output_dir, params=LCCD_PARAMS, nwbfile=SAVE_IMAGE_MASK)

    # the entry asks for the masks; with no rows there is no mask column at all
    (entry,) = info["nwbfile"][NWBDATASET.ROI].values()
    assert entry["image_mask_shape"] == (H, W)
    masks, _ = write_and_read(tmp_path, images, info["nwbfile"])

    assert masks == []


@pytest.mark.parametrize(
    "nwb_settings",
    [
        None,
        {},
        {"ophys": None},
        {"ophys": {"plane_segmentation": {"name": "PlaneSegmentation"}}},
        {"ophys": {"plane_segmentation": {"save_image_mask": False}}},
    ],
)
def test_the_roi_entry_asks_for_no_image_mask_unless_the_setting_is_on(
    nwb_settings,
):
    assert nwb_roi_table([], (H, W), nwb_settings) == {"roi_list": []}


def test_the_roi_entry_carries_the_frame_shape_when_the_setting_is_on():
    entry = nwb_roi_table([], np.array([H, W]), SAVE_IMAGE_MASK)
    assert entry == {"roi_list": [], "image_mask_shape": (H, W)}
    assert all(type(n) is int for n in entry["image_mask_shape"])
