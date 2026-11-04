"""A remote key or request id must never place a local file outside its root."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from studio.app.common.core.storage import mock_storage_controller as mock_module
from studio.app.common.core.storage.mock_storage_controller import MockStorageController
from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    local_path_for_remote_key,
)
from studio.app.common.core.storage.s3_storage_controller import S3StorageController
from studio.app.common.core.utils.filepath_creater import InvalidPathError
from studio.app.common.routers.outputs import _ensure_visualization_synced
from studio.app.dir_path import DIRPATH

S3_BASE = S3StorageController.S3_BASE_PATH


class TestLocalPathForRemoteKey:
    def test_strips_only_the_leading_prefix(self):
        path = local_path_for_remote_key("in/1/in/x.tif", "in/", "/root")
        assert path == "/root/1/in/x.tif"

    def test_absolute_remainder_stays_under_the_root(self):
        assert local_path_for_remote_key("in//etc/x", "in/", "/root") == "/root/etc/x"

    def test_dotdot_key_is_skipped(self, caplog):
        assert local_path_for_remote_key("in/1/../../x", "in/", "/root") is None
        assert "Skip remote key" in caplog.text

    def test_key_without_the_prefix_is_skipped(self, caplog):
        assert local_path_for_remote_key("other/1/x", "in/", "/root") is None
        assert "outside the expected prefix" in caplog.text


@pytest.fixture()
def storage_dirs(tmp_path, monkeypatch):
    """Mock remote storage under tmp_path/remote, local data under tmp_path/local."""
    remote = tmp_path / "remote"
    local = tmp_path / "local" / "data"
    monkeypatch.setenv("REMOTE_STORAGE_TYPE", "1")
    monkeypatch.setattr(MockStorageController, "MOCK_STORAGE_DIR", str(remote))
    monkeypatch.setattr(MockStorageController, "MOCK_INPUT_DIR", str(remote / "input"))
    monkeypatch.setattr(
        MockStorageController, "MOCK_OUTPUT_DIR", str(remote / "output")
    )
    monkeypatch.setattr(DIRPATH, "DATA_DIR", str(local))
    monkeypatch.setattr(DIRPATH, "INPUT_DIR", str(local / "input"))
    monkeypatch.setattr(DIRPATH, "OUTPUT_DIR", str(local / "output"))
    (local / "input").mkdir(parents=True)
    (local / "output").mkdir(parents=True)
    return tmp_path


def _write(path: Path, body: str = "x\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


class TestMockStorageController:
    @pytest.mark.asyncio
    async def test_metas_copy_skips_a_remote_path_that_leaves_output_dir(
        self, storage_dirs, monkeypatch
    ):
        remote_output = Path(MockStorageController.MOCK_OUTPUT_DIR)
        good = _write(remote_output / "1" / "ok" / DIRPATH.EXPERIMENT_YML)
        _write(storage_dirs / "escape" / DIRPATH.EXPERIMENT_YML)
        escaping = f"{remote_output}/1/../../../escape/{DIRPATH.EXPERIMENT_YML}"
        unprefixed = str(storage_dirs / "escape" / DIRPATH.EXPERIMENT_YML)
        monkeypatch.setattr(
            mock_module, "glob", lambda *a, **k: [escaping, unprefixed, str(good)]
        )

        assert await MockStorageController().download_all_experiments_metas(["1"])

        local_output = Path(DIRPATH.OUTPUT_DIR)
        assert (local_output / "1" / "ok" / DIRPATH.EXPERIMENT_YML).is_file()
        assert not (storage_dirs / "local" / "escape").exists()
        assert sorted(p.name for p in local_output.iterdir()) == ["1"]

    @pytest.mark.asyncio
    async def test_experiment_meta_refuses_an_escaping_id(self, storage_dirs):
        with pytest.raises(InvalidPathError):
            await MockStorageController().download_experiment_meta("1", "../../etc")


def _s3_client():
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    return client


class TestS3StorageController:
    @pytest.mark.asyncio
    async def test_input_download_keeps_every_key_under_the_requested_input(
        self, storage_dirs
    ):
        controller = S3StorageController("bucket")
        keys = [
            {"Key": f"{S3_BASE}/input/1/data/../../../etc/passwd", "Size": 1},
            {"Key": f"{S3_BASE}/input/1/data/../../2/x.tif", "Size": 1},
            {"Key": f"{S3_BASE}/input/1/data2/x.tif", "Size": 1},
            {"Key": "elsewhere/input/1/data/x.tif", "Size": 1},
            {"Key": f"{S3_BASE}/input/1/data/folder/", "Size": 0},
            {"Key": f"{S3_BASE}/input/1/data/ok.tif", "Size": 1},
        ]
        targets = []

        async def record(s3_client, s3_file_path, local_file_path, file_size, **kw):
            targets.append(local_file_path)
            return True

        with patch.object(
            controller, "_S3StorageController__get_s3_client", return_value=_s3_client()
        ), patch.object(
            controller, "_list_s3_objects_paginated", AsyncMock(return_value=keys)
        ), patch.object(
            controller, "_download_s3_with_update_check", record
        ):
            assert await controller.download_input_data("1", "data") is True

        assert targets == [f"{DIRPATH.INPUT_DIR}/1/data/ok.tif"]

    @pytest.mark.asyncio
    async def test_input_download_of_a_single_file_ignores_its_siblings(
        self, storage_dirs
    ):
        controller = S3StorageController("bucket")
        keys = [
            {"Key": f"{S3_BASE}/input/1/ok.tif", "Size": 1},
            {"Key": f"{S3_BASE}/input/1/ok.tif.bak", "Size": 1},
        ]
        targets = []

        async def record(s3_client, s3_file_path, local_file_path, file_size, **kw):
            targets.append(local_file_path)
            return True

        with patch.object(
            controller, "_S3StorageController__get_s3_client", return_value=_s3_client()
        ), patch.object(
            controller, "_list_s3_objects_paginated", AsyncMock(return_value=keys)
        ), patch.object(
            controller, "_download_s3_with_update_check", record
        ):
            assert await controller.download_input_data("1", "ok.tif") is True

        assert targets == [f"{DIRPATH.INPUT_DIR}/1/ok.tif"]

    @pytest.mark.asyncio
    async def test_input_download_of_only_a_folder_marker_is_missing(
        self, storage_dirs
    ):
        controller = S3StorageController("bucket")
        keys = [{"Key": f"{S3_BASE}/input/1/data/", "Size": 0}]
        with patch.object(
            controller, "_S3StorageController__get_s3_client", return_value=_s3_client()
        ), patch.object(
            controller, "_list_s3_objects_paginated", AsyncMock(return_value=keys)
        ):
            assert await controller.download_input_data("1", "data") is False

    @pytest.mark.asyncio
    async def test_experiment_download_skips_keys_outside_the_experiment(
        self, storage_dirs
    ):
        controller = S3StorageController("bucket")
        keys = [
            {"Key": f"{S3_BASE}/output/1/exp/../../../etc/passwd", "Size": 1},
            {"Key": f"{S3_BASE}/output/1/exp10/x.pkl", "Size": 1},
            {"Key": f"{S3_BASE}/output/1/exp/ok.pkl", "Size": 1},
        ]
        targets = []

        async def record(s3_client, s3_file_path, local_file_path, file_size, **kw):
            targets.append(local_file_path)
            return True

        listing = AsyncMock(return_value=keys)
        with patch.object(
            controller, "_S3StorageController__get_s3_client", return_value=_s3_client()
        ), patch.object(
            controller, "_list_s3_objects_paginated", listing
        ), patch.object(
            controller, "_download_s3_with_update_check", record
        ):
            assert await controller.download_experiment("1", "exp") is True

        assert targets == [f"{DIRPATH.OUTPUT_DIR}/1/exp/ok.pkl"]
        # The trailing slash keeps "exp10/" out of the listing and its file cap.
        assert listing.call_args.args[1] == f"{S3_BASE}/output/1/exp/"

    @pytest.mark.asyncio
    async def test_all_metas_download_skips_an_experiment_prefix_with_dotdot(
        self, storage_dirs
    ):
        controller = S3StorageController("bucket")
        client = _s3_client()
        client.list_objects_v2.side_effect = [
            {"CommonPrefixes": [{"Prefix": f"{S3_BASE}/output/1/"}]},
            {
                "CommonPrefixes": [
                    {"Prefix": f"{S3_BASE}/output/1/../../../etc/"},
                    {"Prefix": f"{S3_BASE}/output/1/ok/"},
                ]
            },
        ]
        targets = []

        async def record(bucket, key, local_path):
            targets.append(local_path)

        client.download_file = record

        with patch.object(
            controller, "_S3StorageController__get_s3_client", return_value=client
        ):
            await controller.download_all_experiments_metas(["1"])

        assert {Path(t).parent for t in targets} == {
            Path(DIRPATH.OUTPUT_DIR) / "1" / "ok"
        }
        assert not (storage_dirs / "local" / "etc").exists()

    @pytest.mark.asyncio
    async def test_experiment_meta_refuses_an_escaping_id(self, storage_dirs):
        controller = S3StorageController("bucket")
        with patch.object(
            controller, "_S3StorageController__get_s3_client", return_value=_s3_client()
        ):
            with pytest.raises(InvalidPathError):
                await controller.download_experiment_meta("1", "../../etc")


class TestOutputsVisualizationSync:
    @pytest.mark.asyncio
    async def test_refuses_a_sibling_sharing_the_output_dir_prefix(self, monkeypatch):
        monkeypatch.setattr(RemoteStorageController, "is_available", lambda: True)
        with patch("studio.app.common.routers.outputs.ExptOutputPathIds") as ids:
            with pytest.raises(InvalidPathError):
                await _ensure_visualization_synced(
                    f"{DIRPATH.OUTPUT_DIR}_evil/1/exp", "bucket"
                )
        ids.assert_not_called()
