import shutil

import pytest

from studio.app.common.core.storage.remote_storage_controller import (
    RemoteSyncLockFileUtil,
)
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.dir_path import DIRPATH

ROI_ACTIONS = [
    ("status", None),
    ("add_roi", {"posx": 1, "posy": 1, "sizex": 2, "sizey": 2}),
    ("merge_roi", {"ids": [0, 1]}),
    ("delete_roi", {"ids": [0]}),
    ("promote_roi", {"ids": [0]}),
    ("commit_edit", None),
    ("cancel_edit", None),
]


def roi_url(workspace_in_path, action, authorized_workspace):
    return (
        f"/api/visualizations/image/{workspace_in_path}/uid/suite2p_roi_x/"
        f"cell_roi.json/{action}?workspace_id={authorized_workspace}"
    )


@pytest.mark.parametrize("action,body", ROI_ACTIONS)
def test_a_path_in_another_workspace_is_refused(client, action, body):
    # The owner of workspace 1 names a node under workspace 2
    res = client.post(roi_url(2, action, 1), json=body)
    assert res.status_code == 403
    assert "workspace" in res.json()["detail"]


def test_a_path_in_the_authorized_workspace_passes_the_binding(client):
    # No node exists there, so the request fails after the binding, not at it
    res = client.post(roi_url(1, "cancel_edit", 1))
    assert res.status_code == 400


MUTATING_ACTIONS = [a for a in ROI_ACTIONS if a[0] not in ("status", "commit_edit")]


@pytest.mark.parametrize("action,body", MUTATING_ACTIONS)
def test_an_edit_during_a_commit_is_refused_with_423(client, action, body):
    RemoteSyncLockFileUtil.create_sync_lock_file("1", "uid")
    try:
        res = client.post(roi_url(1, action, 1), json=body)
    finally:
        shutil.rmtree(join_filepath([DIRPATH.OUTPUT_DIR, "1", "uid"]))
    assert res.status_code == 423
