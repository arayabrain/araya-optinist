import os
import shutil

from studio.app.common.core.storage.mock_storage_controller import MockStorageController
from studio.app.dir_path import DIRPATH

workspace_id = "default"
unique_id = "0123"

# Copy output test data to the configured output directory and mock storage.
# The DownloadCoordinator requires mock storage to have the data so it can
# sync experiment configs (experiment.yaml, workflow.yaml) correctly.
TEST_DATA_SOURCE_DIR = os.path.join(DIRPATH.ROOT_DIR, "studio", "test_data")
output_src = f"{TEST_DATA_SOURCE_DIR}/output_test/{workspace_id}/{unique_id}"
output_dst = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}"
if not os.path.exists(output_dst) or not os.path.samefile(output_src, output_dst):
    shutil.copytree(output_src, output_dst, dirs_exist_ok=True)

mock_output_dst = f"{MockStorageController.MOCK_OUTPUT_DIR}/{workspace_id}/{unique_id}"
if not os.path.exists(mock_output_dst) or not os.path.samefile(
    output_src, mock_output_dst
):
    shutil.copytree(output_src, mock_output_dst, dirs_exist_ok=True)


def test_import(client):
    response = client.get(f"/workflow/reproduce/{workspace_id}/{unique_id}")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)
