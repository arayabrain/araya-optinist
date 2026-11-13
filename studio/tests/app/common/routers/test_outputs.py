import json
import os
import shutil

import numpy as np
import pytest

from studio.app.common.dataclass import TimeSeriesData
from studio.app.common.dataclass.timeseries_chunk_handler import TimeSeriesChunkHandler
from studio.app.common.routers import outputs
from studio.app.dir_path import DIRPATH
from studio.app.optinist.dataclass import BehaviorData

# Test data source is always in the repo at studio/test_data/
# Use ROOT_DIR to find it regardless of where DATA_DIR is configured
TEST_DATA_SOURCE_DIR = os.path.join(DIRPATH.ROOT_DIR, "studio", "test_data")

workspace_id = "default"
unique_id = "0123"

# Copy output test data to the configured output directory
# This ensures tests work both locally and in Docker/CI
output_src = f"{TEST_DATA_SOURCE_DIR}/output_test/{workspace_id}/{unique_id}"
output_dst = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}"
if not os.path.exists(output_dst) or not os.path.samefile(output_src, output_dst):
    shutil.copytree(output_src, output_dst, dirs_exist_ok=True)

timeseries_dirpath = (
    f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/func1/fluorescence.json"
)


def test_inittimedata(client):
    response = client.get(f"/api/visualizations/inittimedata/{timeseries_dirpath}")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)
    assert isinstance(data["data"], dict)
    assert isinstance(data["std"], dict)
    assert isinstance(data["xrange"], list)

    assert len(data["data"]) == 67
    assert len(data["data"]["0"]) > 1

    for key, value in data["data"].items():
        if key in {"0", "1", "2", "3", "4"}:
            assert len(value) == 1000
        else:
            assert len(value) == 1


def test_timedata(client):
    index = 0
    response = client.get(
        f"/api/visualizations/timedata/{timeseries_dirpath}/?index={index}"
    )
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)
    assert isinstance(data["data"], dict)
    assert isinstance(data["std"], dict)
    assert isinstance(data["xrange"], list)

    assert str(index) in data["data"]
    assert data["data"]["0"]["0"] == 479.916595459

    index = 1
    response = client.get(
        f"/api/visualizations/timedata/{timeseries_dirpath}/?index={index}"
    )
    data = response.json()

    assert response.status_code == 200

    assert str(index) in data["data"]
    assert data["data"]["1"]["0"] == 488.6315612793


def test_alltimedata(client):
    response = client.get(f"/api/visualizations/alltimedata/{timeseries_dirpath}")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)
    assert isinstance(data["data"], dict)
    assert isinstance(data["std"], dict)
    assert isinstance(data["xrange"], list)

    assert len(data["data"]) == 67

    for value in data["data"].values():
        assert len(value) == 1000


def test_behaviour_output_previews_one_trace_per_column(client):
    table = np.column_stack([np.linspace(0, 1, 50), np.arange(50) % 2])
    node_dir = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/behaviour_node"
    BehaviorData(table, file_name="behaviors_data").save_json(node_dir)
    dirpath = f"{node_dir}/behaviors_data"

    init = client.get(f"/api/visualizations/inittimedata/{dirpath}").json()
    second = client.get(f"/api/visualizations/timedata/{dirpath}/?index=1").json()

    assert sorted(init["data"]) == ["0", "1"]
    assert len(init["xrange"]) == 50 and len(init["data"]["0"]) == 50
    assert len(init["data"]["1"]) == 50
    assert [second["data"]["1"][str(t)] for t in range(50)] == list(table[:, 1])


def test_empty_time_range_output_initialises_with_its_records(client):
    node_dir = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/empty_slice_node"
    BehaviorData(np.zeros((0, 2)), file_name="behaviors_data").save_json(node_dir)

    response = client.get(f"/api/visualizations/inittimedata/{node_dir}/behaviors_data")

    assert response.status_code == 200, response.text
    assert response.json()["xrange"] == []
    assert response.json()["data"] == {"0": {}, "1": {}}


def full_records(init):
    return sorted(k for k, v in init["data"].items() if len(v) > 1)


@pytest.mark.parametrize(
    "params, max_points, full",
    [
        ({}, 500_000, ["0", "1", "2"]),
        ({"isFull": True}, 500_000, ["0"]),
        ({}, 10, ["0", "1"]),
    ],
)
def test_init_returns_the_first_chunks_records_in_full(
    client, monkeypatch, params, max_points, full
):
    monkeypatch.setattr(TimeSeriesChunkHandler, "CHUNK_SIZE", 3)
    monkeypatch.setattr(outputs, "INIT_FULL_POINTS", max_points)
    node_dir = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/chunked_node"
    traces = np.arange(28, dtype=float).reshape(7, 4)
    TimeSeriesData(traces, std=traces / 10, file_name="seven").save_json(node_dir)

    response = client.get(
        f"/api/visualizations/inittimedata/{node_dir}/seven", params=params
    )
    init = response.json()

    assert response.status_code == 200, response.text
    assert len(init["data"]) == 7
    assert full_records(init) == full
    assert sorted(k for k, v in init["std"].items() if len(v) > 1) == full
    assert init["data"]["1"].get("3") == (7.0 if "1" in full else None)


def test_init_reads_no_record_past_the_points_budget(client, monkeypatch, caplog):
    monkeypatch.setattr(outputs, "INIT_FULL_POINTS", 3)
    dirpath = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/budget_node/rois"
    shutil.rmtree(dirpath, ignore_errors=True)
    os.makedirs(dirpath)
    record = json.dumps({"data": {"0": 0.0, "1": 1.0, "2": 2.0}})
    for i in range(3):
        with open(os.path.join(dirpath, f"{i}.json"), "w") as f:
            f.write("{not json" if i == 1 else record)

    response = client.get(f"/api/visualizations/inittimedata/{dirpath}")

    assert response.status_code == 200, response.text
    assert full_records(response.json()) == ["0"]
    assert "Timeseries init read stopped" not in caplog.text


@pytest.mark.parametrize("corrupt, full", [(None, list("01234")), ("2", ["0", "1"])])
def test_init_returns_legacy_records_in_full_until_one_fails(client, corrupt, full):
    dirpath = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/legacy_node/rois"
    shutil.rmtree(dirpath, ignore_errors=True)
    os.makedirs(dirpath)
    for i in range(7):
        with open(os.path.join(dirpath, f"{i}.json"), "w") as f:
            if str(i) == corrupt:
                f.write("{not json")
            else:
                json.dump({"data": {str(t): float(i * 10 + t) for t in range(3)}}, f)

    response = client.get(f"/api/visualizations/inittimedata/{dirpath}")
    init = response.json()

    assert response.status_code == 200, response.text
    assert len(init["data"]) == 7
    assert full_records(init) == full


# Test data for image test
tif_workspace_id = "1"
tif_filepath = "test.tif"

# Copy input test data for image test
input_src = f"{TEST_DATA_SOURCE_DIR}/input/{tif_workspace_id}"
input_dst = f"{DIRPATH.INPUT_DIR}/{tif_workspace_id}"
if not os.path.exists(input_dst) or not os.path.samefile(input_src, input_dst):
    shutil.copytree(input_src, input_dst, dirs_exist_ok=True)


def test_image(client):
    response = client.get(
        f"/api/visualizations/image/{tif_filepath}?workspace_id={tif_workspace_id}"
    )
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)
