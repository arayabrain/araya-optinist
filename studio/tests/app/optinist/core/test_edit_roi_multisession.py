import os
import shutil

import numpy as np
import pytest
import yaml

from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.utils.pickle_handler import PickleWriter
from studio.app.dir_path import DIRPATH
from studio.app.optinist.core.edit_ROI.edit_ROI import CellType, EditROI
from studio.app.optinist.core.edit_ROI.utils import EditRoiUtils
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData
from studio.app.optinist.schemas.roi import RoiPos

# Unlike caiman_cnmf's, a cnmf_multisession node id has no "caiman" in it.
NODE_ID = "cnmf_multisession_0000000000"
NUM_ROI = 3
SHAPE = (6, 6)
NUM_FRAME = 5
WORKSPACE = f"{DIRPATH.OUTPUT_DIR}/multisession_edit_ws"
DFF = np.arange(NUM_ROI * NUM_FRAME, dtype=np.float32).reshape(NUM_ROI, NUM_FRAME)
RATE = 10.0


@pytest.fixture(autouse=True)
def isolated_commit(monkeypatch):
    monkeypatch.setattr(
        RemoteStorageController, "is_available", staticmethod(lambda: False)
    )
    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(lambda cls, *a: None))


@pytest.fixture
def file_path():
    # the edit route gates on substrings of this path, so no test name in it
    node_dirpath = f"{WORKSPACE}/00000000/{NODE_ID}"
    os.makedirs(node_dirpath, exist_ok=True)
    node = {
        "type": "AlgorithmNode",
        "data": {
            "label": "cnmf_multisession",
            "param": {},
            "path": "caiman/cnmf_multisession",
            "type": "algorithm",
        },
        "position": {"x": 0, "y": 0},
        "style": {},
    }
    with open(f"{os.path.dirname(node_dirpath)}/workflow.yaml", "w") as f:
        yaml.dump({"nodeDict": {NODE_ID: node}, "edgeDict": {}}, f)

    im = np.full((NUM_ROI, *SHAPE), np.nan)
    for i in range(NUM_ROI):
        im[i, i, :] = i
    PickleWriter.write(
        pickle_path=f"{node_dirpath}/cnmf_multisession.pkl",
        info={
            "edit_roi_data": EditRoiData(images=np.ones((NUM_FRAME, *SHAPE)), im=im),
            "iscell": IscellData(np.ones(NUM_ROI, dtype=int)),
            "fluorescence": FluoData(np.zeros((NUM_ROI, NUM_FRAME))),
            "dff": FluoData(DFF, file_name="dff"),
            "nwbfile": {"input": {"imaging_plane": {"imaging_rate": RATE}}},
        },
    )
    yield f"{node_dirpath}/cell_roi.json"
    shutil.rmtree(WORKSPACE, ignore_errors=True)


@pytest.mark.asyncio
async def test_a_multisession_commit_goes_through_the_caiman_commit(file_path):
    gate = EditRoiUtils.get_algo(f"{NODE_ID}/cell_roi.json")
    assert gate == "cnmf_multisession"  # the commit route's gate

    EditROI(file_path=file_path).add(RoiPos(posx=4, posy=4, sizex=2, sizey=2))
    await EditROI(file_path=file_path).commit()

    committed = EditROI(file_path=file_path).output_info
    assert list(committed["iscell"].data) == [CellType.ROI] * (NUM_ROI + 1)
    np.testing.assert_array_equal(committed["dff"].data[:NUM_ROI], DFF)
    assert np.isnan(committed["dff"].data[NUM_ROI]).all()


@pytest.mark.asyncio
async def test_a_delete_only_commit_keeps_the_recording_rate(file_path):
    EditROI(file_path=file_path).delete([0])
    await EditROI(file_path=file_path).commit()

    nwbfile = EditROI(file_path=file_path).output_info["nwbfile"]["cnmf_multisession"]
    series = nwbfile[NWBDATASET.FLUORESCENCE][NODE_ID]["Fluorescence"]
    assert series["rate"] == RATE


@pytest.mark.asyncio
async def test_a_commit_exports_binary_footprints_as_nwb_masks(file_path):
    EditROI(file_path=file_path).delete([2])
    await EditROI(file_path=file_path).commit()

    nwbfile = EditROI(file_path=file_path).output_info["nwbfile"]["cnmf_multisession"]
    masks = [roi["image_mask"] for roi in nwbfile[NWBDATASET.ROI][NODE_ID]["roi_list"]]
    for i in range(2):
        expected = np.zeros(SHAPE)
        expected[i, :] = 1
        np.testing.assert_array_equal(masks[i], expected)
