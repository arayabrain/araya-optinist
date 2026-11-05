import asyncio
import concurrent.futures
import os
import shutil

import httpx
import pytest

from studio.__main_unit__ import app
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteSyncLockFileUtil,
)
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.dir_path import DIRPATH
from studio.app.optinist.core.edit_ROI import utils as edit_roi_utils
from studio.app.optinist.routers import roi
from studio.app.optinist.schemas.roi import RoiStatus
from studio.tests.app.common.loop_probe import Hold, health_while_held, wait_until

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


@pytest.mark.parametrize("action,body", ROI_ACTIONS)
def test_every_roi_call_during_a_commit_is_refused_with_423(
    client, monkeypatch, action, body
):
    # status included: opening the node runs the interrupted-publish recovery,
    # which must never race the publish it would be recovering from. Without
    # remote storage the lazy-sync reader does not refuse it first.
    monkeypatch.setattr(RemoteStorageController, "is_available", lambda: False)
    RemoteSyncLockFileUtil.create_sync_lock_file("1", "uid")
    try:
        res = client.post(roi_url(1, action, 1), json=body)
    finally:
        shutil.rmtree(join_filepath([DIRPATH.OUTPUT_DIR, "1", "uid"]))
    assert res.status_code == 423


def test_a_commit_whose_movie_is_missing_answers_409_with_the_reason(
    client, monkeypatch
):
    from studio.app.optinist.core.edit_ROI import EditRoiUtils
    from studio.app.optinist.dataclass.roi import EditImagesUnavailableError

    async def missing(*args, **kwargs):
        raise EditImagesUnavailableError("The movie ... is missing (mc_images.tif)")

    monkeypatch.setattr(EditRoiUtils, "execute", missing)
    res = client.post(roi_url(1, "commit_edit", 1))
    assert res.status_code == 409
    assert "mc_images.tif" in res.json()["detail"]


EDIT_ACTIONS = [
    (action, body) for action, body in ROI_ACTIONS if action != "commit_edit"
]


def fake_edit_roi(on_open):
    """EditROI with on_open(file_path) for the node reads, and no-op edits."""

    class FakeEditROI:
        def __init__(self, file_path):
            on_open(file_path)

        def get_status(self):
            return RoiStatus(temp_add_roi=[], temp_merge_roi=[], temp_delete_roi=[])

        def add(self, pos):
            pass

        merge = delete = promote = add

        def cancel(self):
            pass

    return FakeEditROI


@pytest.fixture
def local_storage(monkeypatch):
    monkeypatch.setattr(RemoteStorageController, "is_available", lambda: False)


@pytest.mark.asyncio
@pytest.mark.parametrize("action,body", EDIT_ACTIONS)
async def test_health_is_answered_while_an_roi_call_reads_the_node(
    monkeypatch, local_storage, action, body
):
    hold = Hold()
    monkeypatch.setattr(roi, "EditROI", fake_edit_roi(hold))

    held, health = await health_while_held(
        hold, "POST", roi_url(1, action, 1), json=body
    )

    assert health.status_code == 200
    assert held.status_code == 200, held.text
    assert hold.released_by_answer == [True]


@pytest.mark.asyncio
async def test_a_second_roi_edit_waits_for_the_first(monkeypatch, local_storage):
    first_node = "suite2p_roi_x"
    hold, opened = Hold(), []

    def on_open(file_path):
        opened.append(os.path.basename(os.path.dirname(file_path)))
        if opened[-1] == first_node:
            hold()

    monkeypatch.setattr(roi, "EditROI", fake_edit_roi(on_open))
    second_url = roi_url(1, "add_roi", 1).replace(first_node, "suite2p_roi_y")
    body = dict(ROI_ACTIONS)["add_roi"]

    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        first = asyncio.create_task(http.post(roi_url(1, "add_roi", 1), json=body))
        await wait_until(hold.started)
        second = asyncio.create_task(http.post(second_url, json=body))
        # time for the second edit to start, were edits run side by side
        await asyncio.sleep(0.2)
        opened_while_first_runs = list(opened)
        health = await http.get("/health")
        hold.answered.set()
        responses = await asyncio.gather(first, second)

    assert health.status_code == 200
    assert hold.released_by_answer == [True]
    assert opened_while_first_runs == [first_node]
    assert [r.status_code for r in responses] == [200, 200]


class PendingCommit:
    """The commit worker pool, its commit finishing once `done` is set."""

    def __init__(self):
        self.done = concurrent.futures.Future()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def submit(self, *args, **kwargs):
        return self.done


@pytest.fixture
def commit_pool(monkeypatch, local_storage):
    pool = PendingCommit()
    monkeypatch.setattr(edit_roi_utils, "ProcessPoolExecutor", lambda **kwargs: pool)
    yield pool
    if not pool.done.done():
        pool.done.set_result(None)
    shutil.rmtree(join_filepath([DIRPATH.OUTPUT_DIR, "1", "uid"]), ignore_errors=True)


@pytest.mark.asyncio
async def test_a_commit_takes_the_lock_after_the_edit_under_way(
    monkeypatch, commit_pool
):
    hold, events = Hold(), []

    def on_open(file_path):
        events.append("edit")
        hold()

    take_lock = RemoteSyncLockFileUtil.create_sync_lock_file.__func__

    def recording_take_lock(cls, *args):
        events.append("lock")
        return take_lock(cls, *args)

    monkeypatch.setattr(roi, "EditROI", fake_edit_roi(on_open))
    monkeypatch.setattr(
        RemoteSyncLockFileUtil,
        "create_sync_lock_file",
        classmethod(recording_take_lock),
    )
    body = dict(ROI_ACTIONS)["add_roi"]

    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        edit = asyncio.create_task(http.post(roi_url(1, "add_roi", 1), json=body))
        await wait_until(hold.started)
        commit = asyncio.create_task(http.post(roi_url(1, "commit_edit", 1)))
        # time for the commit to take the lock, were it not to wait
        await asyncio.sleep(0.2)
        events_while_editing = list(events)
        hold.answered.set()
        commit_pool.done.set_result(None)
        responses = await asyncio.gather(edit, commit)

    assert hold.released_by_answer == [True]
    assert events_while_editing == ["edit"]
    assert events == ["edit", "lock"]
    assert [r.status_code for r in responses] == [200, 200]


@pytest.mark.asyncio
async def test_an_edit_queued_behind_a_commit_is_refused_with_423(
    monkeypatch, commit_pool
):
    hold, opened = Hold(), []

    def on_open(file_path):
        opened.append(file_path)
        if len(opened) == 1:
            hold()

    monkeypatch.setattr(roi, "EditROI", fake_edit_roi(on_open))
    body = dict(ROI_ACTIONS)["add_roi"]

    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        first = asyncio.create_task(http.post(roi_url(1, "add_roi", 1), json=body))
        await wait_until(hold.started)
        commit = asyncio.create_task(http.post(roi_url(1, "commit_edit", 1)))
        await asyncio.sleep(0.1)
        # no lock yet, so this one passes the route's check and waits for the thread
        queued = asyncio.create_task(http.post(roi_url(1, "add_roi", 1), json=body))
        await asyncio.sleep(0.2)
        hold.answered.set()
        queued_response = await queued
        commit_pool.done.set_result(None)
        responses = await asyncio.gather(first, commit)

    assert hold.released_by_answer == [True]
    assert queued_response.status_code == 423
    assert len(opened) == 1
    assert [r.status_code for r in responses] == [200, 200]
