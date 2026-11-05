"""Output items the DataView dialog requests at once: stored images from
``GET /api/visualizations/image`` and timeseries records from ``/inittimedata``
and ``/timedata``, read and encoded off the event loop.

TIFF previews are ``test_outputs_tiff_preview.py``'s.
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
from fastapi.routing import serialize_response
from fastapi.utils import create_response_field

from studio.__main_unit__ import app
from studio.app.common.core.utils.file_reader import JsonReader
from studio.app.common.core.utils.json_writer import JsonWriter
from studio.app.common.dataclass import TimeSeriesData
from studio.app.common.dataclass.utils import create_images_list
from studio.app.common.routers import outputs
from studio.app.common.schemas.outputs import JsonTimeSeriesData, PlotMetaData
from studio.app.dir_path import DIRPATH

WORKSPACE_ID = "test_dialog_items"
NODE_PATH = f"{WORKSPACE_ID}/0123/suite2p_roi"
IMAGE = np.array([[0.5, np.nan], [2.0, 3.25]])
IMAGE_META = PlotMetaData(title="mean image")
TRACES = np.array([[1.5, 2.0, 2.5], [3.0, np.nan, 4.0]])


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
    JsonWriter.write_plot_meta(path, "meanImg", IMAGE_META)
    TimeSeriesData(
        TRACES,
        std=TRACES / 10,
        file_name="fluorescence",
        meta=PlotMetaData(xlabel="frames", title="fluorescence"),
    ).save_json(path)
    TimeSeriesData(TRACES, file_name="spks").save_json(path)
    TimeSeriesData(np.zeros((0, 3)), index=np.arange(3), file_name="no_rois").save_json(
        path
    )
    yield path
    shutil.rmtree(os.path.join(DIRPATH.OUTPUT_DIR, WORKSPACE_ID), ignore_errors=True)


def image_url(name):
    return (
        f"/api/visualizations/image/{NODE_PATH}/{name}.json"
        f"?workspace_id={WORKSPACE_ID}"
    )


def timeseries_url(route, name):
    url = f"/api/visualizations/{route}/{NODE_PATH}/{name}"
    return f"{url}?index=1" if route == "timedata" else url


class Hold:
    """Holds a read until the other request is answered."""

    def __init__(self):
        self.reading, self.answered = threading.Event(), threading.Event()
        # True when the answer, not the timeout, released the read
        self.released_by_answer = []

    def __call__(self):
        self.reading.set()
        # Read on the event loop, this holds the other request: it times out
        self.released_by_answer.append(self.answered.wait(2))


async def get_while_held(hold, held_url, other_url):
    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        held = asyncio.create_task(http.get(held_url))
        for _ in range(500):
            if hold.reading.is_set():
                break
            await asyncio.sleep(0.01)
        other = await http.get(other_url)
        hold.answered.set()
        return await held, other


def test_an_image_output_is_served_as_written_with_its_plot_meta(client, node_dir):
    response = client.get(image_url("meanImg"))

    assert response.status_code == 200, response.text
    with open(os.path.join(node_dir, "meanImg.json")) as f:
        stored = json.load(f)
    # columns and index keep their written types: no response model re-encoding
    assert response.json() == {**stored, "meta": asdict(IMAGE_META)}
    assert response.json()["data"] == [[[0.5, None], [2.0, 3.25]]]
    assert b"\n" not in response.content


@pytest.mark.asyncio
async def test_another_request_is_answered_while_an_image_item_is_read(
    node_dir, monkeypatch
):
    hold = Hold()
    read_as_plot_meta = JsonReader.read_as_plot_meta.__func__

    def slow_read_as_plot_meta(cls, filepath):
        if os.path.basename(filepath) == "meanImg.plot-meta.json":
            hold()
        return read_as_plot_meta(cls, filepath)

    monkeypatch.setattr(
        JsonReader, "read_as_plot_meta", classmethod(slow_read_as_plot_meta)
    )

    held, other = await get_while_held(hold, image_url("meanImg"), image_url("Vcorr"))

    assert other.status_code == 200, other.text
    assert held.status_code == 200, held.text
    assert hold.released_by_answer == [True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route, name",
    [
        ("inittimedata", "fluorescence"),
        ("inittimedata", "spks"),
        ("inittimedata", "no_rois"),
        ("timedata", "fluorescence"),
        ("timedata", "spks"),
    ],
    ids=["init_std", "init", "init_no_records", "record_std", "record"],
)
async def test_a_timeseries_response_is_what_the_response_model_gave(
    node_dir, route, name
):
    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        response = await http.get(timeseries_url(route, name))

    assert response.status_code == 200, response.text
    dirpath = os.path.join(node_dir, name)
    if route == "inittimedata":
        data = outputs._init_timeseries_data(dirpath, None)
    else:
        data = outputs._timeseries_record_data(dirpath, 1, None)
    field = create_response_field(name="Response", type_=JsonTimeSeriesData)
    assert response.json() == await serialize_response(
        field=field, response_content=data
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["inittimedata", "timedata"])
async def test_another_request_is_answered_while_a_timeseries_record_is_read(
    node_dir, monkeypatch, route
):
    hold = Hold()
    load_timeseries_record = outputs._load_timeseries_record

    def slow_load_timeseries_record(dirpath, record_id):
        if os.path.basename(dirpath) == "fluorescence":
            hold()
        return load_timeseries_record(dirpath, record_id)

    monkeypatch.setattr(outputs, "_load_timeseries_record", slow_load_timeseries_record)

    held, other = await get_while_held(
        hold, timeseries_url(route, "fluorescence"), timeseries_url(route, "spks")
    )

    assert other.status_code == 200, other.text
    assert held.status_code == 200, held.text
    assert hold.released_by_answer == [True]
