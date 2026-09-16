import json
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

# non_cell_roi is rebuilt on every commit, for all four wrappers and all four
# operations. vacant_roi is the one wrapper importable in CI, so it stands in.
NUM_ROI = 3
SHAPE = (4, 4)
NUM_FRAME = 5
WORKSPACE = f"{DIRPATH.OUTPUT_DIR}/non_cell_projection_ws"


@pytest.fixture(autouse=True)
def no_remote_storage(monkeypatch):
    # commit() uploads to S3 wherever remote storage is configured and rewrites
    # whole.nwb; neither has anything to do with the projection.
    monkeypatch.setattr(
        RemoteStorageController, "is_available", staticmethod(lambda: False)
    )
    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(lambda cls, *a: None))


@pytest.fixture
def node_dirpath(request):
    # Under OUTPUT_DIR, because ExptOutputPathIds reads workspace/unique/function
    # off the path and commit() dispatches on that function_id.
    yield f"{WORKSPACE}/{request.node.name}/vacant_roi_00000000000"
    shutil.rmtree(WORKSPACE, ignore_errors=True)


def build_node(node_dirpath, iscell):
    os.makedirs(node_dirpath, exist_ok=True)

    # commit() reads workflow.yaml to find the nodes downstream of this one; a
    # lone node has none, so nothing else is touched.
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
            "iscell": IscellData(np.array(iscell)),
            "fluorescence": FluoData(np.zeros((NUM_ROI, NUM_FRAME))),
            "nwbfile": {},
        },
    )
    # __non_cell_roi_file_name resolves the name from whichever file is on disk.
    # Seeded empty, so a projection that is never regenerated reads as "nothing
    # drawn" rather than blowing up on the wrong shape.
    rows = list(range(SHAPE[0]))
    with open(f"{node_dirpath}/non_cell_roi.json", "w") as f:
        json.dump(
            {
                "columns": rows,
                "index": rows,
                "data": [[None] * SHAPE[1] for _ in rows],
            },
            f,
        )
    return f"{node_dirpath}/cell_roi.json"


def drawn_ids(node_dirpath, name):
    with open(f"{node_dirpath}/{name}.json") as f:
        projection = np.array(json.load(f)["data"], dtype=float)
    return sorted({int(v) for v in projection[~np.isnan(projection)]})


@pytest.mark.asyncio
async def test_commit_regenerates_non_cell_roi_from_current_iscell(node_dirpath):
    file_path = build_node(node_dirpath, [CellType.ROI, CellType.ROI, CellType.NON_ROI])

    EditROI(file_path=file_path).delete([1])
    await EditROI(file_path=file_path).commit()

    # The demoted ROI has to arrive in non_cell_roi. Before this the projection
    # kept whatever the detection node first wrote, so it never appeared there.
    assert drawn_ids(node_dirpath, "non_cell_roi") == [1, 2]
    assert drawn_ids(node_dirpath, "cell_roi") == [0]


@pytest.mark.asyncio
async def test_commit_promotes_out_of_non_cell_roi(node_dirpath):
    file_path = build_node(
        node_dirpath, [CellType.ROI, CellType.NON_ROI, CellType.NON_ROI]
    )

    EditROI(file_path=file_path).promote([2])
    await EditROI(file_path=file_path).commit()

    assert drawn_ids(node_dirpath, "non_cell_roi") == [1]
    assert drawn_ids(node_dirpath, "cell_roi") == [0, 2]


@pytest.mark.asyncio
async def test_non_cell_roi_is_all_nan_when_every_roi_is_a_cell(node_dirpath):
    file_path = build_node(node_dirpath, [CellType.ROI, CellType.ROI, CellType.NON_ROI])

    EditROI(file_path=file_path).promote([2])
    await EditROI(file_path=file_path).commit()

    assert drawn_ids(node_dirpath, "non_cell_roi") == []


@pytest.mark.asyncio
async def test_non_cell_roi_excludes_rows_with_no_fluorescence_record(node_dirpath):
    # Deleting every ROI empties F while im keeps its rows. Drawing those would
    # offer a click that answers 500.
    file_path = build_node(node_dirpath, [CellType.ROI] * NUM_ROI)

    EditROI(file_path=file_path).delete([0, 1, 2])
    await EditROI(file_path=file_path).commit()

    assert drawn_ids(node_dirpath, "non_cell_roi") == []
    assert drawn_ids(node_dirpath, "cell_roi") == []


@pytest.mark.asyncio
async def test_deleting_the_last_cell_while_non_cells_remain_commits(node_dirpath):
    # The wrapper's all-deleted special case does not fire while committed
    # non-cells are present, so the normal path reduces an empty selection.
    file_path = build_node(
        node_dirpath, [CellType.ROI, CellType.NON_ROI, CellType.NON_ROI]
    )

    EditROI(file_path=file_path).delete([0])
    await EditROI(file_path=file_path).commit()

    assert drawn_ids(node_dirpath, "cell_roi") == []
    assert drawn_ids(node_dirpath, "non_cell_roi") == [0, 1, 2]


@pytest.mark.asyncio
async def test_iscell_keeps_one_entry_per_im_row(node_dirpath):
    file_path = build_node(node_dirpath, [CellType.ROI, CellType.ROI, CellType.NON_ROI])

    EditROI(file_path=file_path).merge([0, 1])
    await EditROI(file_path=file_path).commit()

    edit_roi = EditROI(file_path=file_path)
    assert len(edit_roi.tmp_iscell) == edit_roi.tmp_data.im.shape[0]
