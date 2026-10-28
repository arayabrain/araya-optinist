from dataclasses import replace

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader

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
