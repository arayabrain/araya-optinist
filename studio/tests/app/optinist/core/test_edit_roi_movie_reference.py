"""EditROI with a node whose EditRoiData holds a reference to its movie."""
import asyncio
import os
import shutil

import numpy as np
import pytest
import yaml

from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.utils.pickle_handler import PickleReader, PickleWriter
from studio.app.common.dataclass.image import ImageData
from studio.app.dir_path import DIRPATH
from studio.app.optinist.core.edit_ROI.edit_ROI import CellType, EditROI
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData
from studio.app.optinist.dataclass.roi import EditImagesUnavailableError
from studio.app.optinist.schemas.roi import RoiPos

SHAPE, NUM_FRAME = (6, 6), 5
WORKSPACE = f"{DIRPATH.OUTPUT_DIR}/movie_reference_ws"


@pytest.fixture(autouse=True)
def no_remote_storage(monkeypatch):
    monkeypatch.setattr(
        RemoteStorageController, "is_available", staticmethod(lambda: False)
    )
    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(lambda cls, *a: None))


@pytest.fixture
def node(request):
    """A vacant_roi node with two committed ROIs whose movie file is gone."""
    node_dirpath = f"{WORKSPACE}/{request.node.name[:24]}/vacant_roi_00000000000"
    os.makedirs(node_dirpath, exist_ok=True)
    node_id = os.path.basename(node_dirpath)
    # commit() reads workflow.yaml for the nodes downstream; a lone node has none
    node_def = {
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
        yaml.dump({"nodeDict": {node_id: node_def}, "edgeDict": {}}, f)
    im = np.full((2, *SHAPE), np.nan)
    im[0, 0, :] = 0
    im[1, 3, :] = 1
    # an upstream output in the same experiment, deleted since
    movie = ImageData([f"{os.path.dirname(node_dirpath)}/caiman_mc_x/mc_images.tif"])
    PickleWriter.write(
        pickle_path=f"{node_dirpath}/vacant_roi.pkl",
        info={
            "edit_roi_data": EditRoiData(images=movie, im=im),
            "iscell": IscellData(np.array([CellType.ROI, CellType.ROI])),
            "fluorescence": FluoData(np.ones((2, NUM_FRAME))),
            "nwbfile": {},
        },
    )
    yield f"{node_dirpath}/cell_roi.json", movie
    shutil.rmtree(WORKSPACE, ignore_errors=True)


def test_deleting_rois_commits_without_the_movie(node):
    path, movie = node
    EditROI(file_path=path).delete([1])
    asyncio.run(EditROI(file_path=path).commit())

    committed = PickleReader.read(f"{os.path.dirname(path)}/vacant_roi.pkl")
    assert list(committed["iscell"].data) == [CellType.ROI, CellType.NON_ROI]
    # the commit keeps the reference, not a copy of the movie
    assert committed["edit_roi_data"].images.path == movie.path


def test_adding_a_roi_without_the_movie_names_the_missing_file(node):
    path, _ = node
    EditROI(file_path=path).add(RoiPos(posx=2, posy=2, sizex=2, sizey=2))
    with pytest.raises(EditImagesUnavailableError, match="mc_images.tif"):
        asyncio.run(EditROI(file_path=path).commit())
