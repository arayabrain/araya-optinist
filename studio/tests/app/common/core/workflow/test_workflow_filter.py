import os
import shutil
from glob import glob
from pathlib import Path

import numpy as np
import pytest
import yaml

from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.utils.pickle_handler import PickleWriter
from studio.app.common.core.workflow.workflow import (
    DataFilterParam,
    DataFilterRangeParam,
)
from studio.app.common.core.workflow.workflow_filter import WorkflowNodeDataFilter
from studio.app.const import ORIGINAL_DATA_EXT
from studio.app.dir_path import DIRPATH
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData

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


WORKSPACE, UNIQUE_ID, NODE_ID = "filter_reset_ws", "00000000", "caiman_cnmf_0000"


@pytest.fixture
def ran_node_dir(monkeypatch, with_dff):
    """A finished caiman_cnmf node directory with the JSON its run wrote."""
    monkeypatch.setattr(WorkflowNodeDataFilter, "_check_data_filter", lambda _: None)
    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(lambda cls, *a: None))
    node_dirpath = f"{DIRPATH.OUTPUT_DIR}/{WORKSPACE}/{UNIQUE_ID}/{NODE_ID}"
    os.makedirs(node_dirpath)
    node = {
        "type": "AlgorithmNode",
        "data": {
            "label": "caiman_cnmf",
            "param": {},
            "path": "caiman/caiman_cnmf",
            "type": "algorithm",
        },
        "position": {"x": 0, "y": 0},
        "style": {},
    }
    with open(f"{os.path.dirname(node_dirpath)}/workflow.yaml", "w") as f:
        yaml.dump({"nodeDict": {NODE_ID: node}, "edgeDict": {}}, f)

    output = roi_node_output("caiman_cnmf", with_dff)
    output["cell_roi"] = RoiData(
        np.nanmax(output["edit_roi_data"].im[[0, 2]], axis=0),
        output_dir=node_dirpath,
        file_name="cell_roi",
    )
    output["non_cell_roi"] = RoiData(
        np.nanmax(output["edit_roi_data"].im[[1]], axis=0),
        output_dir=node_dirpath,
        file_name="non_cell_roi",
    )
    PickleWriter.write(f"{node_dirpath}/caiman_cnmf.pkl", output)
    for value in output.values():
        if isinstance(value, (FluoData, RoiData)):
            value.save_json(node_dirpath)
    yield node_dirpath
    shutil.rmtree(f"{DIRPATH.OUTPUT_DIR}/{WORKSPACE}", ignore_errors=True)


def json_files(node_dirpath):
    paths = glob(f"{node_dirpath}/**/*.json", recursive=True)
    return {p: Path(p).read_text() for p in paths if ORIGINAL_DATA_EXT not in p}


@pytest.mark.parametrize("with_dff", [True, False])
def test_a_filter_reset_restores_every_json_the_run_wrote(ran_node_dir, with_dff):
    ran = json_files(ran_node_dir)

    WorkflowNodeDataFilter(WORKSPACE, UNIQUE_ID, NODE_ID).filter_node_data(PARAM)
    filtered = json_files(ran_node_dir)
    WorkflowNodeDataFilter(WORKSPACE, UNIQUE_ID, NODE_ID).filter_node_data(None)

    changed = {p for p in ran if filtered[p] != ran[p]}
    assert f"{ran_node_dir}/non_cell_roi.json" in changed
    assert any("/fluorescence/" in p for p in changed)
    assert any("/dff/" in p for p in changed) == with_dff
    assert {p: v for p, v in json_files(ran_node_dir).items() if p in ran} == ran
    assert os.path.isdir(f"{ran_node_dir}/dff") == with_dff
    assert glob(f"{ran_node_dir}/*{ORIGINAL_DATA_EXT}") == []
