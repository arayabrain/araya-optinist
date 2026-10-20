"""TIFF previews served by ``GET /api/visualizations/image``.

The frame reads themselves are ``test_json_writer.py``'s; these cover the
route: the window bound as an HTTP answer, and how builds share the process.
"""

import asyncio
import os
import shutil
import time

import numpy as np
import pytest
import tifffile
from fastapi import HTTPException

from studio.app.common.core.utils import json_writer
from studio.app.common.routers import outputs
from studio.app.dir_path import DIRPATH

WORKSPACE_ID = "test_tiff_preview"
STACK = np.arange(30 * 8 * 6, dtype=np.uint16).reshape(30, 8, 6)


@pytest.fixture
def input_dir():
    path = os.path.join(DIRPATH.INPUT_DIR, WORKSPACE_ID)
    os.makedirs(path, exist_ok=True)
    tifffile.imwrite(os.path.join(path, "stack.tif"), STACK)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def preview_lock(monkeypatch):
    # A lock of the test's own, so contention never binds the module's to its loop
    lock = asyncio.Lock()
    monkeypatch.setattr(outputs, "_tiff_preview_lock", lock)
    return lock


def get_preview(client, start_index, end_index):
    return client.get(
        "/api/visualizations/image/stack.tif",
        params={
            "workspace_id": WORKSPACE_ID,
            "start_index": start_index,
            "end_index": end_index,
        },
    )


def test_a_window_is_served_from_the_frames_it_names(client, input_dir):
    response = get_preview(client, 3, 7)

    assert response.status_code == 200, response.text
    assert response.json()["data"] == STACK[2:7].tolist()


def test_a_window_over_the_budget_is_422_with_the_range_that_fits(
    client, input_dir, monkeypatch
):
    monkeypatch.setattr(json_writer, "TIFF_PREVIEW_MAX_PIXELS", 4 * 8 * 6)

    response = get_preview(client, 1, 10)

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "Frames 1-10 are too many to preview at 8 x 6; choose at most 4 frames."
    )
    assert not os.path.exists(os.path.join(input_dir, "stack"))
    # the refusal released the build lock
    assert get_preview(client, 1, 4).status_code == 200


@pytest.mark.asyncio
async def test_builds_run_one_at_a_time(preview_lock, tmp_path, monkeypatch):
    running, overlap = [], []

    def build(tiff_filepath, save_dirpath, start_index, end_index):
        running.append(start_index)
        overlap.append(len(running))
        time.sleep(0.1)
        running.remove(start_index)

    monkeypatch.setattr(outputs, "save_tiff2json", build)

    await asyncio.gather(
        *(
            outputs._build_tiff_preview(
                "stack.tif", str(tmp_path), str(tmp_path / f"{i}.json"), i, i + 9
            )
            for i in (1, 11, 21)
        )
    )

    assert overlap == [1, 1, 1]


@pytest.mark.asyncio
async def test_a_build_does_not_block_the_event_loop(
    preview_lock, tmp_path, monkeypatch
):
    monkeypatch.setattr(outputs, "save_tiff2json", lambda *args: time.sleep(0.3))
    ticks = 0

    async def tick():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        await outputs._build_tiff_preview(
            "stack.tif", str(tmp_path), str(tmp_path / "1.json"), 1, 10
        )
    finally:
        ticker.cancel()

    assert ticks >= 10


@pytest.mark.asyncio
async def test_a_request_queued_behind_the_same_window_does_not_rebuild_it(
    preview_lock, tmp_path, monkeypatch
):
    json_filepath = tmp_path / "stack_1_10.json"
    builds = []

    def build(*args):
        builds.append(args)
        time.sleep(0.1)
        json_filepath.write_text("{}")

    monkeypatch.setattr(outputs, "save_tiff2json", build)

    await asyncio.gather(
        *(
            outputs._build_tiff_preview(
                "stack.tif", str(tmp_path), str(json_filepath), 1, 10
            )
            for _ in range(3)
        )
    )

    assert len(builds) == 1


@pytest.mark.asyncio
async def test_a_request_that_waits_too_long_is_429(
    preview_lock, tmp_path, monkeypatch
):
    monkeypatch.setattr(outputs, "TIFF_PREVIEW_WAIT_SECONDS", 0.05)

    async with preview_lock:
        with pytest.raises(HTTPException) as excinfo:
            await outputs._build_tiff_preview(
                "stack.tif", str(tmp_path), str(tmp_path / "1.json"), 1, 10
            )

    assert excinfo.value.status_code == 429
    assert excinfo.value.headers == {"Retry-After": "5"}
    # the timed-out wait left the lock free
    assert not preview_lock.locked()
