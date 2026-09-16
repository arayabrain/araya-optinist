import asyncio
import os
import shutil

import numpy as np
import pytest

from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
)
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.core.utils.pickle_handler import PickleReader, PickleWriter
from studio.app.common.core.workflow.workflow import (
    Edge,
    Node,
    NodeData,
    NodePosition,
    Style,
)
from studio.app.common.core.workflow.workflow_writer import WorkflowConfigWriter
from studio.app.dir_path import DIRPATH
from studio.app.optinist.core.edit_ROI.edit_ROI import CellType, EditROI
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData

workspace_id = "default"
unique_id = "edit_roi_invalidation"
workflow_dir = join_filepath([DIRPATH.OUTPUT_DIR, workspace_id, unique_id])

# parent -> roi -> child -> grandchild, plus a sibling fed by the parent only
NODES = {
    "parent": ("motion_correction_p1", "motion_correction"),
    "roi": ("lccd_cell_detection_r1", "lccd_cell_detection"),
    "child": ("pca_c1", "pca"),
    "grandchild": ("cca_g1", "cca"),
    "sibling": ("eta_s1", "eta"),
}
EDGES = [
    ("parent", "roi"),
    ("roi", "child"),
    ("child", "grandchild"),
    ("parent", "sibling"),
]
FRAMES, SHAPE = 5, (4, 4)


def pickle_path(key):
    node_id, label = NODES[key]
    return join_filepath([workflow_dir, node_id, f"{label}.pkl"])


def build_experiment():
    shutil.rmtree(workflow_dir, ignore_errors=True)

    def node(key):
        node_id, label = NODES[key]
        return Node(
            id=node_id,
            type="AlgorithmNode",
            data=NodeData(label=label, param={}, path=label, type="algorithm"),
            position=NodePosition(x=0, y=0),
            style=Style(),
        )

    def edge(src, dst):
        return Edge(
            id=f"{src}-{dst}",
            type="default",
            animated=False,
            source=NODES[src][0],
            sourceHandle="",
            target=NODES[dst][0],
            targetHandle="",
            style=Style(),
        )

    WorkflowConfigWriter(
        workspace_id,
        unique_id,
        {NODES[k][0]: node(k) for k in NODES},
        {f"{s}-{d}": edge(s, d) for s, d in EDGES},
    ).write()

    for key in ("parent", "child", "grandchild", "sibling"):
        create_directory(os.path.dirname(pickle_path(key)))
        PickleWriter.write(pickle_path(key), {"stub": key})

    roi_dir = os.path.dirname(pickle_path("roi"))
    create_directory(roi_dir)
    images = np.arange(FRAMES * SHAPE[0] * SHAPE[1], dtype=float).reshape(
        FRAMES, *SHAPE
    )
    im = np.full((2, *SHAPE), np.nan)
    im[0, 0, :] = 0
    im[1, 1, :] = 1
    PickleWriter.write(
        pickle_path("roi"),
        {
            "fluorescence": FluoData(np.ones((2, FRAMES)), file_name="fluorescence"),
            "iscell": IscellData(np.array([CellType.ROI, CellType.ROI])),
            "cell_roi": RoiData(
                np.nanmax(im, axis=0), output_dir=roi_dir, file_name="cell_roi"
            ),
            "edit_roi_data": EditRoiData(images=images, im=im),
            "nwbfile": {"input": {"stub": True}, NODES["roi"][1]: {}},
        },
    )


@pytest.fixture
def experiment(monkeypatch):
    build_experiment()
    saved_nwb = []
    monkeypatch.setattr(
        Runner,
        "save_all_nwb",
        classmethod(lambda cls, path, nwb: saved_nwb.append((path, nwb))),
    )
    monkeypatch.setattr(RemoteStorageController, "is_available", lambda: False)
    yield saved_nwb
    shutil.rmtree(workflow_dir, ignore_errors=True)


def test_commit_keeps_the_edit_and_invalidates_only_downstream_nodes(experiment):
    roi_pickle = pickle_path("roi")
    edit_roi = EditROI(roi_pickle)
    edit_roi.delete([0])
    assert os.path.exists(edit_roi.tmp_pickle_file_path)

    asyncio.run(EditROI(roi_pickle).commit())

    info = PickleReader.read(roi_pickle)
    assert list(info["iscell"].data) == [CellType.NON_ROI, CellType.ROI]
    assert info["edit_roi_data"].delete_roi == [0]
    assert not os.path.exists(edit_roi.tmp_pickle_file_path)

    assert not os.path.exists(pickle_path("child"))
    assert not os.path.exists(pickle_path("grandchild"))
    assert os.path.exists(pickle_path("parent"))
    assert os.path.exists(pickle_path("sibling"))


def test_commit_regenerates_whole_nwb_once_from_the_roi_node(experiment):
    roi_pickle = pickle_path("roi")
    EditROI(roi_pickle).delete([0])

    asyncio.run(EditROI(roi_pickle).commit())

    assert len(experiment) == 1
    path, nwbfile = experiment[0]
    assert path == join_filepath([workflow_dir, "whole.nwb"])
    assert "input" in nwbfile
    assert NODES["roi"][1] in nwbfile
