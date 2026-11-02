import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
from studio.app.common.core.storage.mock_storage_controller import MockStorageController
from studio.app.dir_path import DIRPATH

workspace_id = "default"
unique_id = "0123"


def test_import(client):
    response = client.get(f"/workflow/reproduce/{workspace_id}/{unique_id}")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)


def test_reproduce_keeps_data_usage(client, monkeypatch):
    read = ExptConfigReader.read
    monkeypatch.setattr(
        ExptConfigReader,
        "read",
        classmethod(lambda cls, ws, uid: replace(read(ws, uid), data_usage=1234)),
    )
    response = client.get(f"/workflow/reproduce/{workspace_id}/{unique_id}")
    assert response.json()["data_usage"] == 1234


SAMPLE_WORKSPACE_ID = "9875"
SAMPLE_DATA_DIR = Path(DIRPATH.ROOT_DIR) / "sample_data"
SAMPLES = sorted(
    (category, uid.name)
    for category in ["tutorial", "maintenance"]
    for uid in (SAMPLE_DATA_DIR / category / "output").iterdir()
)


@pytest.fixture()
def sample_workspace(monkeypatch, tmp_path):
    monkeypatch.setattr(MockStorageController, "MOCK_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(MockStorageController, "MOCK_INPUT_DIR", f"{tmp_path}/input")
    monkeypatch.setattr(MockStorageController, "MOCK_OUTPUT_DIR", f"{tmp_path}/output")
    yield
    for folder in ["input", "output"]:
        shutil.rmtree(
            Path(DIRPATH.DATA_DIR) / folder / SAMPLE_WORKSPACE_ID, ignore_errors=True
        )


# The samples ship workflow yaml only, so a claimed output can only 404
@pytest.mark.parametrize("category, uid", SAMPLES)
def test_a_reproduced_sample_claims_no_outputs(client, sample_workspace, category, uid):
    assert client.get(f"/workflow/sample_data/{SAMPLE_WORKSPACE_ID}/{category}").json()

    response = client.get(f"/workflow/reproduce/{SAMPLE_WORKSPACE_ID}/{uid}")
    data = response.json()

    assert response.status_code == 200
    assert data["hasNWB"] is False
    for node in data["function"].values():
        assert (node["outputPaths"], node["hasNWB"]) == (None, False)
