import os
import shutil

from studio.app.common.core.storage.mock_storage_controller import MockStorageController
from studio.app.dir_path import DIRPATH

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

# Also seed mock storage so the DownloadCoordinator can sync from it.
# The coordinator clears local data before re-copying from remote storage,
# so the mock storage must contain the test data for it to be restored.
mock_output_dst = f"{MockStorageController.MOCK_OUTPUT_DIR}/{workspace_id}/{unique_id}"
if not os.path.exists(mock_output_dst) or not os.path.samefile(
    output_src, mock_output_dst
):
    shutil.copytree(output_src, mock_output_dst, dirs_exist_ok=True)

timeseries_dirpath = (
    f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/func1/fluorescence.json"
)


def test_inittimedata(client):
    response = client.get(f"/outputs/inittimedata/{timeseries_dirpath}")
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
    response = client.get(f"/outputs/timedata/{timeseries_dirpath}/?index={index}")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)
    assert isinstance(data["data"], dict)
    assert isinstance(data["std"], dict)
    assert isinstance(data["xrange"], list)

    assert str(index) in data["data"]
    assert data["data"]["0"]["0"] == 479.916595459

    index = 1
    response = client.get(f"/outputs/timedata/{timeseries_dirpath}/?index={index}")
    data = response.json()

    assert response.status_code == 200

    assert str(index) in data["data"]
    assert data["data"]["1"]["0"] == 488.6315612793


def test_alltimedata(client):
    response = client.get(f"/outputs/alltimedata/{timeseries_dirpath}")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)
    assert isinstance(data["data"], dict)
    assert isinstance(data["std"], dict)
    assert isinstance(data["xrange"], list)

    assert len(data["data"]) == 67

    for value in data["data"].values():
        assert len(value) == 1000


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
        f"/outputs/image/{tif_filepath}?workspace_id={tif_workspace_id}"
    )
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)
