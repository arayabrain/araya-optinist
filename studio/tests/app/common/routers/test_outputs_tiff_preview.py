"""TIFF previews served by ``GET /api/visualizations/image``.

The frame reads themselves are ``test_json_writer.py``'s; these cover the
route: the window bound as an HTTP answer, and how previews share the process.
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


@pytest.fixture
def reads(monkeypatch):
    """Stub the preview read; each entry is the path read."""
    paths = []

    def read(filepath):
        paths.append(filepath)
        return filepath

    monkeypatch.setattr(outputs.JsonReader, "read_as_output", read)
    return paths


def get_preview(client, start_index, end_index):
    return client.get(
        "/api/visualizations/image/stack.tif",
        params={
            "workspace_id": WORKSPACE_ID,
            "start_index": start_index,
            "end_index": end_index,
        },
    )


def read_preview(tmp_path, json_name, start_index=1, end_index=10):
    return outputs._read_tiff_preview(
        "stack.tif", str(tmp_path), str(tmp_path / json_name), start_index, end_index
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
    # the refusal released the lock
    assert get_preview(client, 1, 4).status_code == 200


@pytest.mark.asyncio
async def test_builds_run_one_at_a_time(preview_lock, reads, tmp_path, monkeypatch):
    running, overlap = [], []

    def build(tiff_filepath, save_dirpath, start_index, end_index):
        running.append(start_index)
        overlap.append(len(running))
        time.sleep(0.1)
        running.remove(start_index)

    monkeypatch.setattr(outputs, "save_tiff2json", build)

    await asyncio.gather(
        *(read_preview(tmp_path, f"{i}.json", i, i + 9) for i in (1, 11, 21))
    )

    assert overlap == [1, 1, 1]


@pytest.mark.asyncio
async def test_a_cached_preview_is_not_read_during_another_build(
    preview_lock, tmp_path, monkeypatch
):
    building, read_while_building = [], []

    def build(*args):
        building.append(True)
        time.sleep(0.2)
        building.clear()

    def read(filepath):
        read_while_building.append(bool(building))
        return filepath

    monkeypatch.setattr(outputs, "save_tiff2json", build)
    monkeypatch.setattr(outputs.JsonReader, "read_as_output", read)
    (tmp_path / "cached.json").write_text("{}")

    async def read_cached_once_the_build_runs():
        await asyncio.sleep(0.05)
        return await read_preview(tmp_path, "cached.json")

    await asyncio.gather(
        read_preview(tmp_path, "new.json"), read_cached_once_the_build_runs()
    )

    assert read_while_building == [False, False]


@pytest.mark.asyncio
async def test_a_build_does_not_block_the_event_loop(
    preview_lock, reads, tmp_path, monkeypatch
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
        await read_preview(tmp_path, "1.json")
    finally:
        ticker.cancel()

    assert ticks >= 10


@pytest.mark.asyncio
async def test_a_request_queued_behind_the_same_window_does_not_rebuild_it(
    preview_lock, reads, tmp_path, monkeypatch
):
    builds = []

    def build(*args):
        builds.append(args)
        time.sleep(0.1)
        (tmp_path / "stack_1_10.json").write_text("{}")

    monkeypatch.setattr(outputs, "save_tiff2json", build)

    await asyncio.gather(*(read_preview(tmp_path, "stack_1_10.json") for _ in range(3)))

    assert len(builds) == 1
    assert len(reads) == 3


@pytest.mark.asyncio
async def test_a_request_that_waits_too_long_is_429(
    preview_lock, reads, tmp_path, monkeypatch
):
    monkeypatch.setattr(outputs, "TIFF_PREVIEW_WAIT_SECONDS", 0.05)

    async with preview_lock:
        with pytest.raises(HTTPException) as excinfo:
            await read_preview(tmp_path, "1.json")

    assert excinfo.value.status_code == 429
    assert excinfo.value.headers == {"Retry-After": "5"}
    assert reads == []
    # the timed-out wait left the lock free
    assert not preview_lock.locked()
