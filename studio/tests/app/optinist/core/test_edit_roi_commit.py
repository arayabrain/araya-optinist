import numpy as np
import pytest

from studio.app.optinist.core.edit_ROI.edit_ROI import CellType
from studio.app.optinist.core.edit_ROI.wrappers.caiman_edit_roi import (
    commit_edit as caiman_commit_edit,
)
from studio.app.optinist.core.edit_ROI.wrappers.lccd_edit_roi import (
    commit_edit as lccd_commit_edit,
)
from studio.app.optinist.core.edit_ROI.wrappers.vacant_roi_edit_roi import commit_edit
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData
from studio.app.optinist.dataclass.roi import RoiMasks

# vacant_roi, lccd and caiman share this commit implementation; caiman also
# carries its dff output.
NUM_COMMITTED = 3
FRAMES = 5
SHAPE = (4, 4)


def build_state(iscell):
    """Three committed ROIs plus one appended by a pending merge of ROI 0 and 1."""
    im = np.full((NUM_COMMITTED + 1, *SHAPE), np.nan)
    for i in range(NUM_COMMITTED):
        im[i, i, :] = i
    im[NUM_COMMITTED, 0:2, :] = NUM_COMMITTED

    data = EditRoiData(images=None, roi_masks=RoiMasks.from_stack(im))
    data.temp_merge_roi[float(NUM_COMMITTED)] = [0, 1]

    images = np.arange(FRAMES * SHAPE[0] * SHAPE[1], dtype=float).reshape(
        FRAMES, *SHAPE
    )
    return images, data, FluoData(np.ones((NUM_COMMITTED, FRAMES))), np.array(iscell)


def test_merge_promotes_the_new_roi_and_demotes_its_sources(tmp_path):
    images, data, fluorescence, iscell = build_state(
        [CellType.TEMP_DELETE, CellType.TEMP_DELETE, CellType.ROI, CellType.TEMP_ADD]
    )

    info = commit_edit(images, data, fluorescence, iscell, str(tmp_path), "vacant_roi")

    assert list(info["iscell"].data) == [
        CellType.NON_ROI,
        CellType.NON_ROI,
        CellType.ROI,
        CellType.ROI,
    ]


def test_deleting_a_pending_merge_leaves_it_demoted_with_a_real_trace(tmp_path):
    # The user merged ROI 0 and 1, then deleted the merged ROI before committing.
    images, data, fluorescence, iscell = build_state(
        [
            CellType.TEMP_DELETE,
            CellType.TEMP_DELETE,
            CellType.ROI,
            CellType.TEMP_DELETE,
        ]
    )

    info = commit_edit(images, data, fluorescence, iscell, str(tmp_path), "vacant_roi")

    # The merged ROI stays demoted rather than being resurrected by its still
    # pending temp_merge_roi entry, and its sources are non-cell ROIs the user
    # can promote back separately.
    assert list(info["iscell"].data) == [CellType.NON_ROI] * 2 + [
        CellType.ROI,
        CellType.NON_ROI,
    ]

    # It still gets a real trace, so fluorescence stays one row per ROI and
    # the non_cell_roi view can serve it.
    new_fluorescences = info["fluorescence"].data
    assert len(new_fluorescences) == len(data.roi_masks)
    merged = data.roi_masks.label_image([NUM_COMMITTED])
    expected = np.mean(images[:, ~np.isnan(merged)], axis=1)
    assert np.allclose(new_fluorescences[NUM_COMMITTED], expected)
    assert not np.allclose(new_fluorescences[NUM_COMMITTED], 0)


def test_a_deleted_pending_merge_commits_with_its_sources_kept(tmp_path):
    # The state EditROI.delete leaves for an undone merge: sources restored,
    # merged ROI marked for deletion, and its temp_merge_roi entry kept so the
    # trace is still appended and fluorescence stays aligned with the ROIs.
    images, data, fluorescence, iscell = build_state(
        [CellType.ROI, CellType.ROI, CellType.ROI, CellType.TEMP_DELETE]
    )

    info = commit_edit(images, data, fluorescence, iscell, str(tmp_path), "vacant_roi")

    assert list(info["iscell"].data) == [CellType.ROI] * 3 + [CellType.NON_ROI]
    assert len(info["fluorescence"].data) == len(data.roi_masks)


def test_a_retained_non_cell_is_not_resurrected_when_its_trace_is_recomputed(
    tmp_path,
):
    # The delete-every-ROI path leaves fluorescence empty while the masks keep every
    # row, so "appended in this session" cannot be inferred from the row range
    # alone. Every row still needs a trace to keep F aligned with the ROIs, but only
    # the ones actually marked TEMP_ADD may become cells.
    images, data, fluorescence, iscell = build_state(
        [
            CellType.NON_ROI,
            CellType.NON_ROI,
            CellType.NON_ROI,
            CellType.TEMP_ADD,
        ]
    )
    empty = FluoData(np.zeros((0, FRAMES)))

    info = commit_edit(images, data, empty, iscell, str(tmp_path), "vacant_roi")

    assert list(info["iscell"].data) == [CellType.NON_ROI] * 3 + [CellType.ROI]
    assert len(info["fluorescence"].data) == len(data.roi_masks)


def _committed_dff():
    return FluoData(np.arange(NUM_COMMITTED * FRAMES, dtype=float).reshape(-1, FRAMES))


def _assert_dff_padded(info, dff):
    new_dff = info["dff"].data
    assert len(new_dff) == len(info["fluorescence"].data) == len(info["iscell"].data)
    np.testing.assert_array_equal(new_dff[:NUM_COMMITTED], dff.data)
    assert np.isnan(new_dff[NUM_COMMITTED:]).all()


def test_caiman_dff_follows_a_merge_with_a_nan_row(tmp_path):
    images, data, fluorescence, iscell = build_state(
        [CellType.TEMP_DELETE, CellType.TEMP_DELETE, CellType.ROI, CellType.TEMP_ADD]
    )
    dff = _committed_dff()

    info = caiman_commit_edit(
        images, data, fluorescence, iscell, str(tmp_path), "caiman_cnmf", dff=dff
    )

    _assert_dff_padded(info, dff)


def test_caiman_dff_follows_an_add_with_a_nan_row(tmp_path):
    images, data, fluorescence, iscell = build_state(
        [CellType.ROI, CellType.ROI, CellType.ROI, CellType.TEMP_ADD]
    )
    data.temp_merge_roi = {}
    data.temp_add_roi = {NUM_COMMITTED: None}
    dff = _committed_dff()

    info = caiman_commit_edit(
        images, data, fluorescence, iscell, str(tmp_path), "caiman_cnmf", dff=dff
    )

    _assert_dff_padded(info, dff)
    assert data.add_roi == [NUM_COMMITTED]


def test_caiman_dff_from_an_empty_run_gets_the_movie_time_axis(tmp_path):
    images, data, _, iscell = build_state([CellType.NON_ROI] * 3 + [CellType.TEMP_ADD])

    info = caiman_commit_edit(
        images,
        data,
        FluoData(np.zeros((0, FRAMES))),
        iscell,
        str(tmp_path),
        "caiman_cnmf",
        dff=FluoData(np.zeros((0, FRAMES))),
    )

    assert info["dff"].data.shape == (len(data.roi_masks), FRAMES)
    assert np.isnan(info["dff"].data).all()


def test_a_caiman_pickle_without_dff_commits_without_one(tmp_path):
    images, data, fluorescence, iscell = build_state(
        [CellType.TEMP_DELETE, CellType.TEMP_DELETE, CellType.ROI, CellType.TEMP_ADD]
    )

    info = caiman_commit_edit(
        images, data, fluorescence, iscell, str(tmp_path), "caiman_cnmf"
    )

    assert "dff" not in info


@pytest.mark.parametrize("commit", [commit_edit, lccd_commit_edit])
def test_a_commit_exports_the_rate_and_binary_masks_to_nwb(tmp_path, commit):
    images, data, fluorescence, iscell = build_state(
        [CellType.ROI, CellType.ROI, CellType.TEMP_DELETE, CellType.TEMP_DELETE]
    )

    info = commit(images, data, fluorescence, iscell, str(tmp_path), "node", rate=10.0)

    nwbfile = info["nwbfile"]
    assert nwbfile[NWBDATASET.FLUORESCENCE]["node"]["Fluorescence"]["rate"] == 10.0
    mask = nwbfile[NWBDATASET.ROI]["node"]["roi_list"][0]["image_mask"]
    expected = np.zeros(SHAPE)
    expected[0, :] = 1
    np.testing.assert_array_equal(mask, expected)
