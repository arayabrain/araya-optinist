import numpy as np
import pytest

from studio.app.common.core.workflow.workflow import (
    DataFilterParam,
    DataFilterRangeParam,
)
from studio.app.common.core.workflow.workflow_filter import WorkflowNodeDataFilter
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData

SHAPE = (3, 4)
FRAMES = 6


def roi_node_output(node_type, with_dff=True):
    """Three ROIs, one per image row, ROI 1 a non-cell."""
    im = np.full((3, *SHAPE), np.nan)
    for i in range(3):
        im[i, i, :] = i
    fluo = np.arange(3 * FRAMES, dtype=float).reshape(3, FRAMES)
    info = {
        "edit_roi_data": EditRoiData(images=None, im=im),
        "fluorescence": FluoData(fluo, file_name="fluorescence"),
        "iscell": IscellData(np.array([1, 0, 1])),
        "nwbfile": {node_type: {NWBDATASET.POSTPROCESS: {node_type: {}}}},
    }
    if with_dff:
        info["dff"] = FluoData(fluo / 100, file_name="dff")
    return info


def roi_ids(roi):
    return set(np.unique(roi.data[~np.isnan(roi.data)]))


PARAM = DataFilterParam(
    dim1=[DataFilterRangeParam(start=1, end=4)],
    roi=[DataFilterRangeParam(start=0, end=2)],
)


@pytest.mark.parametrize("with_dff", [True, False])
def test_caiman_regenerates_the_roi_images_with_or_without_dff(tmp_path, with_dff):
    output = roi_node_output("caiman_cnmf", with_dff)

    info = WorkflowNodeDataFilter.filter_data(
        output, PARAM, "caiman_cnmf", str(tmp_path)
    )

    # ROI 2 is filtered out, so it joins ROI 1 in non_cell_roi
    assert roi_ids(info["non_cell_roi"]) == {1, 2}
    assert roi_ids(info["all_roi"]) == {0, 1, 2}
    assert list(info["iscell"].data) == [1, 0, 0]
    assert info["fluorescence"].data.shape == (3, 3)
    if with_dff:
        np.testing.assert_array_equal(info["dff"].data, output["dff"].data[:, 1:4])
    else:
        assert "dff" not in info


def test_lccd_keeps_its_own_roi_images_and_trims_dff(tmp_path):
    output = roi_node_output("lccd_detect")

    info = WorkflowNodeDataFilter.filter_data(
        output, PARAM, "lccd_detect", str(tmp_path)
    )

    assert "all_roi" not in info and "non_cell_roi" not in info
    np.testing.assert_array_equal(info["dff"].data, output["dff"].data[:, 1:4])
