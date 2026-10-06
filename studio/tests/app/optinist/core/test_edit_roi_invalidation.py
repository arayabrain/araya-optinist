import asyncio
import os
import shutil

import numpy as np
import pytest

from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncLockFileUtil,
    RemoteSyncStatusFileUtil,
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


def test_commit_deletes_the_descendants_remotely_and_keeps_the_lock(
    experiment, monkeypatch, tmp_path
):
    from studio.app.common.core.storage.mock_storage_controller import (
        MockStorageController,
    )

    monkeypatch.setenv("REMOTE_STORAGE_TYPE", "1")
    monkeypatch.setattr(RemoteStorageController, "is_available", lambda: True)
    monkeypatch.setattr(MockStorageController, "MOCK_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(MockStorageController, "MOCK_INPUT_DIR", f"{tmp_path}/input")
    monkeypatch.setattr(MockStorageController, "MOCK_OUTPUT_DIR", f"{tmp_path}/output")
    monkeypatch.setattr(
        RemoteSyncStatusFileUtil,
        "get_remote_bucket_name",
        classmethod(lambda cls, ws, uid: "bucket"),
    )
    remote = f"{tmp_path}/output/{workspace_id}/{unique_id}"
    for key in ("child", "grandchild"):
        create_directory(f"{remote}/{NODES[key][0]}")
        PickleWriter.write(f"{remote}/{NODES[key][0]}/{NODES[key][1]}.pkl", {})
    # the descendant may never have been synced locally; the bucket still loses it
    os.remove(pickle_path("grandchild"))
    # the request holds the lock, as EditRoiUtils.execute does, for the whole commit
    token = RemoteSyncLockFileUtil.create_sync_lock_file(workspace_id, unique_id)

    roi_pickle = pickle_path("roi")
    EditROI(roi_pickle).delete([0])
    asyncio.run(EditROI(roi_pickle).commit())

    assert not os.path.exists(f"{remote}/pca_c1/pca.pkl")
    assert not os.path.exists(f"{remote}/cca_g1/cca.pkl")
    assert os.path.exists(f"{remote}/{NODES['roi'][0]}/{NODES['roi'][1]}.pkl")
    assert RemoteSyncStatusFileUtil.check_sync_status_success(workspace_id, unique_id)
    # the writer neither refused the request's own lock nor released it
    assert RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id)
    RemoteSyncLockFileUtil.delete_sync_lock_file(workspace_id, unique_id, token)


def test_a_failure_before_publish_leaves_node_and_descendants_untouched(
    experiment, monkeypatch
):
    roi_pickle = pickle_path("roi")
    edit_roi = EditROI(roi_pickle)
    edit_roi.delete([0])
    monkeypatch.setattr(
        Runner,
        "save_all_nwb",
        classmethod(lambda cls, *a: (_ for _ in ()).throw(OSError("disk full"))),
    )

    with pytest.raises(OSError):
        asyncio.run(EditROI(roi_pickle).commit())

    assert list(PickleReader.read(roi_pickle)["iscell"].data) == [CellType.ROI] * 2
    assert os.path.exists(edit_roi.tmp_pickle_file_path)
    assert os.path.exists(pickle_path("child"))
    assert os.path.exists(pickle_path("grandchild"))
    roi_dir = os.path.dirname(roi_pickle)
    assert not os.path.exists(join_filepath([roi_dir, "tmp_commit.pkl"]))


def test_a_commit_killed_between_retire_and_replace_is_finished_on_next_open(
    experiment,
):
    roi_pickle = pickle_path("roi")
    roi_dir = os.path.dirname(roi_pickle)
    edit_roi = EditROI(roi_pickle)
    edit_roi.delete([0])
    # the state __publish leaves if killed after removing the pending edit:
    # the staged pickle holds the commit, the pending pickle is gone
    staged = PickleReader.read(edit_roi.tmp_pickle_file_path)
    PickleWriter.write(join_filepath([roi_dir, "tmp_commit.pkl"]), staged)
    os.remove(edit_roi.tmp_pickle_file_path)

    EditROI(roi_pickle)

    assert not os.path.exists(join_filepath([roi_dir, "tmp_commit.pkl"]))
    assert list(PickleReader.read(roi_pickle)["iscell"].data) == [
        CellType.TEMP_DELETE,
        CellType.ROI,
    ]

    # whereas a staged pickle beside a still-pending edit never got that far
    EditROI(roi_pickle).delete([1])
    PickleWriter.write(join_filepath([roi_dir, "tmp_commit.pkl"]), {"stale": True})
    EditROI(roi_pickle)
    assert not os.path.exists(join_filepath([roi_dir, "tmp_commit.pkl"]))
    assert "stale" not in PickleReader.read(roi_pickle)


def test_a_staged_pickle_that_never_reached_the_disk_is_discarded(experiment):
    roi_pickle = pickle_path("roi")
    staged = join_filepath([os.path.dirname(roi_pickle), "tmp_commit.pkl"])
    # no pending edit, so it looks like a killed publish; but a host crash
    # can leave the staged file truncated
    with open(staged, "wb") as f:
        f.write(b"\x80\x04\x95")

    EditROI(roi_pickle)

    assert not os.path.exists(staged)
    assert list(PickleReader.read(roi_pickle)["iscell"].data) == [CellType.ROI] * 2
