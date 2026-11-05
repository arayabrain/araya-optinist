"""Stored image outputs served by ``GET /api/visualizations/image``.

TIFF previews are ``test_outputs_tiff_preview.py``'s; these cover the JSON a
node wrote, such as a mean image or an ROI map.
"""

import asyncio
import json
import os
import shutil
import threading
from dataclasses import asdict

import httpx
import numpy as np
import pytest

from studio.__main_unit__ import app
from studio.app.common.core.utils.file_reader import JsonReader
from studio.app.common.core.utils.json_writer import JsonWriter
from studio.app.common.dataclass.utils import create_images_list
from studio.app.common.routers import outputs
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.dir_path import DIRPATH

WORKSPACE_ID = "test_image_items"
NODE_PATH = f"{WORKSPACE_ID}/0123/suite2p_roi"
IMAGE = np.array([[0.5, np.nan], [2.0, 3.25]])
META = PlotMetaData(title="mean image")


@pytest.fixture
def node_dir(monkeypatch):
    async def no_sync(*_):
        pass

    monkeypatch.setattr(outputs, "_ensure_visualization_synced", no_sync)
    path = os.path.join(DIRPATH.OUTPUT_DIR, NODE_PATH)
    for name in ("meanImg", "Vcorr"):
        JsonWriter.write_as_split(
            os.path.join(path, f"{name}.json"), create_images_list(IMAGE)
        )
    JsonWriter.write_plot_meta(path, "meanImg", META)
    yield path
    shutil.rmtree(os.path.join(DIRPATH.OUTPUT_DIR, WORKSPACE_ID), ignore_errors=True)


def image_url(name):
    return (
        f"/api/visualizations/image/{NODE_PATH}/{name}.json"
        f"?workspace_id={WORKSPACE_ID}"
    )


def test_an_image_output_is_served_as_written_with_its_plot_meta(client, node_dir):
    response = client.get(image_url("meanImg"))

    assert response.status_code == 200, response.text
    with open(os.path.join(node_dir, "meanImg.json")) as f:
        stored = json.load(f)
    # columns and index keep their written types: no response model re-encoding
    assert response.json() == {**stored, "meta": asdict(META)}
    assert response.json()["data"] == [[[0.5, None], [2.0, 3.25]]]
    assert b"\n" not in response.content


@pytest.mark.asyncio
async def test_another_request_is_answered_while_an_image_item_is_read(
    node_dir, monkeypatch
):
    reading, other_answered = threading.Event(), threading.Event()
    released_by_answer = []
    read_as_plot_meta = JsonReader.read_as_plot_meta.__func__

    def slow_read_as_plot_meta(cls, filepath):
        if os.path.basename(filepath) == "meanImg.plot-meta.json":
            reading.set()
            # Read on the event loop, this holds the other request: it times out
            released_by_answer.append(other_answered.wait(2))
        return read_as_plot_meta(cls, filepath)

    monkeypatch.setattr(
        JsonReader, "read_as_plot_meta", classmethod(slow_read_as_plot_meta)
    )

    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        slow = asyncio.create_task(http.get(image_url("meanImg")))
        for _ in range(500):
            if reading.is_set():
                break
            await asyncio.sleep(0.01)
        other = await http.get(image_url("Vcorr"))
        other_answered.set()
        slow_response = await slow

    assert other.status_code == 200, other.text
    assert slow_response.status_code == 200, slow_response.text
    assert released_by_answer == [True]
