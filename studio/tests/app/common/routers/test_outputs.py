import os
import shutil

import numpy as np

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
        if key == "0":
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
    assert len(init["data"]["1"]) == 1
    assert [second["data"]["1"][str(t)] for t in range(50)] == list(table[:, 1])


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
