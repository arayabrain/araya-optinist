"""Output items the DataView dialog requests at once: stored images from
``GET /api/visualizations/image`` and timeseries records from ``/inittimedata``
and ``/timedata``, read and encoded off the event loop; per process, images two
at a time at most and timeseries records one at a time.

TIFF previews are ``test_outputs_tiff_preview.py``'s.
"""

import asyncio
import json
import os
import shutil
import threading
import time
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
async def test_image_items_are_read_two_at_a_time_at_most(node_dir, monkeypatch):
    running, overlap = [], []

    def counted_read_as_output_bytes(filepath):
        token = object()
        running.append(token)
        overlap.append(len(running))
        time.sleep(0.1)
        running.remove(token)
        return b'{"data":[]}'

    monkeypatch.setattr(
        JsonReader, "read_as_output_bytes", counted_read_as_output_bytes
    )

    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        responses = await asyncio.gather(
            *(http.get(image_url(name)) for name in ["meanImg", "Vcorr"] * 2)
        )

    assert [r.status_code for r in responses] == [200] * 4
    assert max(overlap) == 2


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
    load_timeseries_records = outputs._load_timeseries_records

    def slow_load_timeseries_records(dirpath, record_ids):
        if os.path.basename(dirpath) == "fluorescence":
            hold()
        return load_timeseries_records(dirpath, record_ids)

    monkeypatch.setattr(
        outputs, "_load_timeseries_records", slow_load_timeseries_records
    )

    # Another timeseries read waits its turn; an image item does not
    held, other = await get_while_held(
        hold, timeseries_url(route, "fluorescence"), image_url("Vcorr")
    )

    assert other.status_code == 200, other.text
    assert held.status_code == 200, held.text
    assert hold.released_by_answer == [True]


@pytest.mark.asyncio
async def test_timeseries_records_are_read_one_at_a_time(node_dir, monkeypatch):
    running, overlap = [], []
    load_timeseries_records = outputs._load_timeseries_records

    def counted_load_timeseries_records(dirpath, record_ids):
        token = object()
        running.append(token)
        overlap.append(len(running))
        time.sleep(0.1)
        running.remove(token)
        return load_timeseries_records(dirpath, record_ids)

    monkeypatch.setattr(
        outputs, "_load_timeseries_records", counted_load_timeseries_records
    )

    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        responses = await asyncio.gather(
            http.get(timeseries_url("inittimedata", "fluorescence")),
            http.get(timeseries_url("inittimedata", "spks")),
            http.get(timeseries_url("timedata", "fluorescence")),
        )

    assert [r.status_code for r in responses] == [200, 200, 200]
    assert overlap == [1, 1, 1]


@pytest.mark.asyncio
async def test_a_timeseries_read_not_started_in_time_is_refused(node_dir, monkeypatch):
    monkeypatch.setattr(outputs, "TIMESERIES_READ_WAIT_SECONDS", 0.3)
    reading, released = threading.Event(), threading.Event()
    loaded = []
    load_timeseries_record = outputs._load_timeseries_record

    def held_load_timeseries_record(dirpath, record_id):
        loaded.append(os.path.basename(dirpath))
        reading.set()
        released.wait(5)
        return load_timeseries_record(dirpath, record_id)

    monkeypatch.setattr(outputs, "_load_timeseries_record", held_load_timeseries_record)

    async with httpx.AsyncClient(app=app, base_url="http://test") as http:
        held = asyncio.create_task(http.get(timeseries_url("timedata", "fluorescence")))
        for _ in range(500):
            if reading.is_set():
                break
            await asyncio.sleep(0.01)
        refused = await http.get(timeseries_url("timedata", "spks"))
        released.set()
        held = await held

    assert refused.status_code == 429, refused.text
    assert refused.headers["Retry-After"] == "5"
    # The held read had started, so it is answered past the wait
    assert held.status_code == 200, held.text
    # The refused read never ran
    assert loaded == ["fluorescence"]


def test_the_image_route_documents_columns_and_index_as_stored():
    schema = app.openapi()
    response = schema["paths"]["/api/visualizations/image/{filepath}"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    model = schema["components"]["schemas"][response["$ref"].rsplit("/", 1)[-1]]

    for name in ("columns", "index"):
        assert model["properties"][name]["items"] == {
            "anyOf": [{"type": "integer"}, {"type": "string"}]
        }
