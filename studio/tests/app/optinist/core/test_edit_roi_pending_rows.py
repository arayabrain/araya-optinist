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
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData
from studio.app.optinist.schemas.roi import RoiPos

# A pending add or merge that is deleted again before commit used to be committed
# anyway, as a non-cell with a trace, so every undone edit grew im, F and iscell
# by one row for good. vacant_roi stands in for all four wrappers.
NUM_ROI = 3
SHAPE = (6, 6)
NUM_FRAME = 5
WORKSPACE = f"{DIRPATH.OUTPUT_DIR}/pending_rows_ws"
ROI_A = RoiPos(posx=1, posy=1, sizex=2, sizey=2)
ROI_B = RoiPos(posx=4, posy=4, sizex=2, sizey=2)


@pytest.fixture(autouse=True)
def isolated_commit(monkeypatch):
    monkeypatch.setattr(
        RemoteStorageController, "is_available", staticmethod(lambda: False)
    )
    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(lambda cls, *a: None))


@pytest.fixture
def file_path(request):
    node_dirpath = f"{WORKSPACE}/{request.node.name}/vacant_roi_00000000000"
    os.makedirs(node_dirpath, exist_ok=True)
    node_id = os.path.basename(node_dirpath)
    node = {
        "type": "AlgorithmNode",
        "data": {
            "label": "vacant_roi",
            "param": {},
            "path": "vacant_roi",
            "type": "algorithm",
        },
        "position": {"x": 0, "y": 0},
        "style": {},
    }
    with open(f"{os.path.dirname(node_dirpath)}/workflow.yaml", "w") as f:
        yaml.dump({"nodeDict": {node_id: node}, "edgeDict": {}}, f)

    im = np.full((NUM_ROI, *SHAPE), np.nan)
    for i in range(NUM_ROI):
        im[i, i, :] = i
    PickleWriter.write(
        pickle_path=f"{node_dirpath}/vacant_roi.pkl",
        info={
            "edit_roi_data": EditRoiData(images=np.ones((NUM_FRAME, *SHAPE)), im=im),
            "iscell": IscellData(
                np.array([CellType.ROI, CellType.ROI, CellType.NON_ROI])
            ),
            "fluorescence": FluoData(np.zeros((NUM_ROI, NUM_FRAME))),
            "nwbfile": {},
        },
    )
    yield f"{node_dirpath}/cell_roi.json"
    shutil.rmtree(WORKSPACE, ignore_errors=True)


def committed(file_path):
    edit_roi = EditROI(file_path=file_path)
    return (
        edit_roi.tmp_data,
        list(edit_roi.tmp_iscell),
        edit_roi.output_info["fluorescence"].data,
    )


@pytest.mark.asyncio
async def test_a_deleted_pending_merge_leaves_no_row(file_path):
    EditROI(file_path=file_path).merge([0, 1])
    EditROI(file_path=file_path).delete([NUM_ROI])
    await EditROI(file_path=file_path).commit()

    data, iscell, fluorescence = committed(file_path)
    assert data.im.shape[0] == NUM_ROI
    assert fluorescence.shape[0] == NUM_ROI
    assert iscell == [CellType.ROI, CellType.ROI, CellType.NON_ROI]
    assert data.merge_roi == [] and data.delete_roi == []


@pytest.mark.asyncio
async def test_surviving_pending_rows_are_renumbered(file_path):
    EditROI(file_path=file_path).add(ROI_A)
    EditROI(file_path=file_path).add(ROI_B)
    EditROI(file_path=file_path).delete([NUM_ROI])
    await EditROI(file_path=file_path).commit()

    data, iscell, fluorescence = committed(file_path)
    assert data.im.shape[0] == NUM_ROI + 1
    assert fluorescence.shape[0] == NUM_ROI + 1
    assert iscell == [CellType.ROI, CellType.ROI, CellType.NON_ROI, CellType.ROI]
    # the surviving add took the freed index, in its pixels too
    drawn = data.im[NUM_ROI][~np.isnan(data.im[NUM_ROI])]
    assert drawn.size > 0 and set(drawn) == {NUM_ROI}
    assert np.isnan(data.im[NUM_ROI, 1, 1]) and not np.isnan(data.im[NUM_ROI, 4, 4])
    assert data.add_roi == [NUM_ROI]


@pytest.mark.asyncio
async def test_a_source_of_a_surviving_pending_merge_is_kept(file_path):
    EditROI(file_path=file_path).add(ROI_A)
    EditROI(file_path=file_path).merge([NUM_ROI, 0])
    EditROI(file_path=file_path).delete([NUM_ROI])
    await EditROI(file_path=file_path).commit()

    data, iscell, fluorescence = committed(file_path)
    assert data.im.shape[0] == NUM_ROI + 2
    assert fluorescence.shape[0] == NUM_ROI + 2
    assert iscell[NUM_ROI] == CellType.NON_ROI and iscell[NUM_ROI + 1] == CellType.ROI
    assert data.merge_roi == [float(NUM_ROI + 1), NUM_ROI, 0, -1.0]
