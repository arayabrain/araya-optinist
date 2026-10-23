import shutil
from pathlib import Path

import pytest

from studio.app.dir_path import DIRPATH

workspace_id = "default"
unique_id = "0123"


def test_import(client):
    response = client.get(f"/workflow/reproduce/{workspace_id}/{unique_id}")
    data = response.json()

    assert response.status_code == 200
    assert isinstance(data, dict)


SAMPLE_WORKSPACE_ID = "9875"
SAMPLE_OUTPUT_DIR = Path(DIRPATH.ROOT_DIR) / "sample_data" / "tutorial" / "output"


@pytest.fixture()
def imported_samples(client):
    assert client.get(f"/workflow/sample_data/{SAMPLE_WORKSPACE_ID}/tutorial").json()
    yield
    for folder in ["input", "output"]:
        shutil.rmtree(
            Path(DIRPATH.DATA_DIR) / folder / SAMPLE_WORKSPACE_ID, ignore_errors=True
        )


# The samples ship workflow yaml only, so a claimed output can only 404
@pytest.mark.parametrize("uid", sorted(p.name for p in SAMPLE_OUTPUT_DIR.iterdir()))
def test_a_reproduced_sample_claims_no_outputs(client, imported_samples, uid):
    response = client.get(f"/workflow/reproduce/{SAMPLE_WORKSPACE_ID}/{uid}")
    data = response.json()

    assert response.status_code == 200
    assert data["hasNWB"] is False
    for node in data["function"].values():
        assert (node["outputPaths"], node["hasNWB"]) == (None, False)
