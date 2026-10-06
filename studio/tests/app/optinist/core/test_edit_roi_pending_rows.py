import os
import shutil

import numpy as np
import pytest
import yaml
from fastapi import HTTPException
from pydantic import ValidationError

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


@pytest.mark.asyncio
async def test_a_merge_of_a_pending_merge_keeps_every_row(file_path):
    # add -> row 3, merge(3, 0) -> row 4, merge(4, 1) -> row 5: row 3 is marked
    # deleted by the first merge, but the second merge still averages from it
    EditROI(file_path=file_path).add(ROI_A)
    EditROI(file_path=file_path).merge([NUM_ROI, 0])
    EditROI(file_path=file_path).merge([NUM_ROI + 1, 1])
    await EditROI(file_path=file_path).commit()

    data, iscell, fluorescence = committed(file_path)
    assert data.im.shape[0] == fluorescence.shape[0] == NUM_ROI + 3
    assert iscell == [CellType.NON_ROI] * (NUM_ROI + 2) + [CellType.ROI]
    assert data.add_roi == [NUM_ROI]
    assert data.merge_roi == [
        float(NUM_ROI + 1),
        NUM_ROI,
        0,
        -1.0,
        float(NUM_ROI + 2),
        NUM_ROI + 1,
        1,
        -1.0,
    ]
    # the first merge's mask is the added ROI plus cell 0, drawn under its own index
    merged = data.im[NUM_ROI + 1]
    assert set(merged[~np.isnan(merged)]) == {NUM_ROI + 1}
    assert not np.isnan(merged[1, 1]) and not np.isnan(merged[0, 0])


@pytest.mark.asyncio
async def test_several_operations_commit_together(file_path):
    EditROI(file_path=file_path).add(ROI_A)
    EditROI(file_path=file_path).delete([1])
    EditROI(file_path=file_path).merge([NUM_ROI, 0])
    await EditROI(file_path=file_path).commit()

    data, iscell, fluorescence = committed(file_path)
    assert data.im.shape[0] == fluorescence.shape[0] == NUM_ROI + 2
    assert iscell == [CellType.NON_ROI] * (NUM_ROI + 1) + [CellType.ROI]
    assert data.add_roi == [NUM_ROI]
    assert data.delete_roi == [1]
    assert data.merge_roi == [float(NUM_ROI + 1), NUM_ROI, 0, -1.0]


@pytest.mark.asyncio
async def test_delete_all_add_delete_all_cycles_keep_rows_aligned(file_path):
    EditROI(file_path=file_path).delete([0, 1])
    await EditROI(file_path=file_path).commit()
    EditROI(file_path=file_path).add(ROI_A)
    await EditROI(file_path=file_path).commit()
    EditROI(file_path=file_path).delete([NUM_ROI])
    await EditROI(file_path=file_path).commit()

    data, iscell, fluorescence = committed(file_path)
    assert data.im.shape[0] == fluorescence.shape[0] == NUM_ROI + 1
    assert iscell == [CellType.NON_ROI] * (NUM_ROI + 1)
    assert data.add_roi == [NUM_ROI] and data.delete_roi == [0, 1, NUM_ROI]

    # every demoted row kept its trace, so any of them can come back
    EditROI(file_path=file_path).promote([NUM_ROI])
    await EditROI(file_path=file_path).commit()
    assert committed(file_path)[1][NUM_ROI] == CellType.ROI


@pytest.mark.asyncio
async def test_a_failed_commit_is_retried_without_applying_the_edit_twice(
    file_path, monkeypatch
):
    EditROI(file_path=file_path).add(ROI_A)
    edit_roi = EditROI(file_path=file_path)

    def fail_once(cls, *args):
        monkeypatch.setattr(Runner, "save_all_nwb", classmethod(lambda cls, *a: None))
        raise RuntimeError("whole.nwb could not be written")

    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(fail_once))
    with pytest.raises(RuntimeError):
        await edit_roi.commit()

    # nothing published: the node is as it was and the edit is still pending
    data, iscell, fluorescence = committed(file_path)
    assert fluorescence.shape[0] == NUM_ROI
    assert data.im.shape[0] == NUM_ROI + 1
    assert iscell[NUM_ROI] == CellType.TEMP_ADD
    assert os.path.exists(edit_roi.tmp_pickle_file_path)

    await EditROI(file_path=file_path).commit()
    await EditROI(file_path=file_path).commit()  # nothing pending: a no-op

    data, iscell, fluorescence = committed(file_path)
    # the no-op commit must not have dropped the movie from the node pickle
    assert EditROI(file_path=file_path).images.shape == (NUM_FRAME, *SHAPE)
    assert data.im.shape[0] == fluorescence.shape[0] == NUM_ROI + 1
    assert iscell == [CellType.ROI, CellType.ROI, CellType.NON_ROI, CellType.ROI]
    assert data.add_roi == [NUM_ROI]
    assert not os.path.exists(edit_roi.tmp_pickle_file_path)


def test_an_roi_covering_no_pixel_is_refused_at_add(file_path):
    with pytest.raises(ValidationError):
        RoiPos(posx=1, posy=1, sizex=0, sizey=2)
    with pytest.raises(HTTPException) as exc:
        EditROI(file_path=file_path).add(RoiPos(posx=50, posy=50, sizex=2, sizey=2))
    assert exc.value.status_code == 400
    assert committed(file_path)[0].im.shape[0] == NUM_ROI


def saved_fluorescence(file_path):
    node_dirpath = os.path.dirname(file_path)
    fluo_dir = f"{node_dirpath}/fluorescence"
    return {
        name: os.path.getsize(f"{fluo_dir}/{name}") for name in os.listdir(fluo_dir)
    }


@pytest.mark.asyncio
async def test_cancel_after_a_failed_commit_restores_the_saved_outputs(
    file_path, monkeypatch
):
    await EditROI(file_path=file_path).commit()  # nothing pending: writes the JSON
    committed_outputs = saved_fluorescence(file_path)

    EditROI(file_path=file_path).add(ROI_A)
    monkeypatch.setattr(
        Runner,
        "save_all_nwb",
        classmethod(lambda cls, *a: (_ for _ in ()).throw(OSError("disk full"))),
    )
    with pytest.raises(OSError):
        await EditROI(file_path=file_path).commit()
    # the outputs written before the failure describe the edit the node lacks
    assert saved_fluorescence(file_path) != committed_outputs

    EditROI(file_path=file_path).cancel()

    assert saved_fluorescence(file_path) == committed_outputs
    assert committed(file_path)[0].im.shape[0] == NUM_ROI
