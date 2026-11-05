"""The storage refresh lists the controller's own S3 input and output prefixes,
so inputs that live only on S3 (another instance uploaded them) are counted."""
import asyncio
from unittest.mock import MagicMock, Mock, patch

from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.scripts import run_sync_data_capacity_cloud as script
from studio.scripts.run_sync_data_capacity_cloud import (
    CloudWorkspaceDataCapacityService as Service,
)


def s3_client_listing(*sizes):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [
        {"Contents": [{"Size": size} for size in sizes]}
    ]
    client.list_objects_v2.return_value = {}
    return client


def listed_prefix(client):
    return client.get_paginator.return_value.paginate.call_args.kwargs["Prefix"]


def test_the_workspace_input_usage_is_read_from_the_input_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(script.DIRPATH, "INPUT_DIR", str(tmp_path))
    client = s3_client_listing(100, 23)
    db = Mock()
    with patch.object(script.boto3, "client", return_value=client):
        asyncio.run(Service.update_workspace_data_usage_with_s3(db, "bucket", "7"))
    assert listed_prefix(client) == S3StorageController.make_s3_input_prefix("7")
    (update,) = db.execute.call_args.args
    assert update.compile().params["input_data_usage"] == 123
    db.commit.assert_called_once()


def test_the_experiments_are_listed_under_the_output_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(script.DIRPATH, "OUTPUT_DIR", str(tmp_path))
    client = s3_client_listing()
    with patch.object(script.boto3, "client", return_value=client), patch.object(
        script.WorkspaceDataCapacityService, "is_available", return_value=False
    ):
        asyncio.run(
            Service.recalculate_workspace_data_capacity_with_s3(None, "bucket", "7")
        )
    listed = client.list_objects_v2.call_args.kwargs["Prefix"]
    assert listed == S3StorageController.make_s3_output_prefix("7")
