"""
Unit tests for the OOM / worker-termination failure-surfacing path
(Phase A, #643).

These verify that when the snakemake worker process is killed mid-run (e.g.
OOM-killed under the container memory cap) the run is surfaced as failed
immediately, instead of being left stuck in "running" until WorkflowMonitor's
~2h timeout.
"""

from concurrent.futures.process import BrokenProcessPool
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MODULE = "studio.app.common.core.snakemake.snakemake_executor"

WORKSPACE_ID = "test_workspace"
UNIQUE_ID = "test_unique_id"


class TestSnakemakeExecuteWorkerKilled:
    """snakemake_execute() must not raise / hang when the worker is killed."""

    def test_broken_process_pool_surfaces_failure(self):
        from studio.app.common.core.snakemake.snakemake_executor import (
            snakemake_execute,
        )

        with (
            patch(f"{MODULE}.ProcessPoolExecutor") as mock_pool,
            patch(f"{MODULE}._surface_terminated_workflow") as mock_surface,
            patch(f"{MODULE}.update_user_storage_after_workflow") as mock_storage,
        ):
            # Context manager must not swallow the raised BrokenProcessPool.
            mock_pool.return_value.__exit__.return_value = False
            executor = mock_pool.return_value.__enter__.return_value
            future = MagicMock()
            future.result.side_effect = BrokenProcessPool("worker killed")
            executor.submit.return_value = future

            # user_id=None -> skip the workflow-count decrement branch.
            result = snakemake_execute(WORKSPACE_ID, UNIQUE_ID, MagicMock(), None)

        assert result is False
        mock_surface.assert_called_once_with(WORKSPACE_ID, UNIQUE_ID)
        # Storage update should be skipped on the failure path.
        mock_storage.assert_not_called()


class TestSurfaceTerminatedWorkflow:
    """_surface_terminated_workflow() mirrors the worker's failure tail."""

    def test_records_error_and_observes_when_no_remote(self):
        from studio.app.common.core.snakemake.snakemake_executor import (
            _surface_terminated_workflow,
        )

        with (
            patch(f"{MODULE}.SmkStatusLogger") as mock_logger,
            patch(f"{MODULE}.RemoteStorageController") as mock_remote,
            patch(f"{MODULE}.WorkflowResult") as mock_wfr,
        ):
            mock_remote.is_available.return_value = False
            mock_wfr.return_value.observe_overall = AsyncMock()

            _surface_terminated_workflow(WORKSPACE_ID, UNIQUE_ID)

            # An error was written so observe() reports has_error=True.
            mock_logger.record_external_error.assert_called_once()
            ws, uid, message = mock_logger.record_external_error.call_args[0]
            assert ws == WORKSPACE_ID
            assert uid == UNIQUE_ID
            assert "memory" in message.lower()

            # Status is refreshed now so the run does not stay "running".
            mock_wfr.return_value.observe_overall.assert_awaited_once()

    def test_writes_remote_error_state_when_remote_available(self):
        from studio.app.common.core.snakemake.snakemake_executor import (
            _surface_terminated_workflow,
        )

        with (
            patch(f"{MODULE}.SmkStatusLogger"),
            patch(f"{MODULE}.RemoteStorageController") as mock_remote,
            patch(f"{MODULE}.RemoteSyncLockFileUtil") as mock_lock,
            patch(f"{MODULE}.RemoteSyncStatusFileUtil") as mock_status,
            patch(f"{MODULE}.WorkflowResult") as mock_wfr,
        ):
            mock_remote.is_available.return_value = True
            mock_wfr.return_value.observe_overall = AsyncMock()
            mock_status.get_remote_bucket_name.return_value = "bucket"

            _surface_terminated_workflow(WORKSPACE_ID, UNIQUE_ID)

            mock_lock.delete_sync_lock_file.assert_called_once_with(
                WORKSPACE_ID, UNIQUE_ID
            )
            mock_status.create_sync_status_file_for_error.assert_called_once()

    def test_observe_failure_is_swallowed(self):
        """A failure inside observe_overall() must not propagate."""
        from studio.app.common.core.snakemake.snakemake_executor import (
            _surface_terminated_workflow,
        )

        with (
            patch(f"{MODULE}.SmkStatusLogger"),
            patch(f"{MODULE}.RemoteStorageController") as mock_remote,
            patch(f"{MODULE}.WorkflowResult") as mock_wfr,
            patch(f"{MODULE}.logger.error"),
        ):
            mock_remote.is_available.return_value = False
            mock_wfr.return_value.observe_overall = AsyncMock(
                side_effect=RuntimeError("observe failed")
            )

            # Should not raise.
            _surface_terminated_workflow(WORKSPACE_ID, UNIQUE_ID)


class TestRecordExternalError:
    """SmkStatusLogger.record_external_error() makes get_error_content() error."""

    def test_appends_error_and_is_detected(self, tmp_path, monkeypatch):
        from studio.app.common.core.snakemake import smk_status_logger as smk_mod
        from studio.app.common.core.snakemake.smk_status_logger import SmkStatusLogger

        monkeypatch.setattr(smk_mod.DIRPATH, "OUTPUT_DIR", str(tmp_path))

        # No error initially.
        assert (
            SmkStatusLogger.get_error_content(WORKSPACE_ID, UNIQUE_ID).has_error
            is False
        )

        SmkStatusLogger.record_external_error(
            WORKSPACE_ID, UNIQUE_ID, "killed by OOM"
        )

        info = SmkStatusLogger.get_error_content(WORKSPACE_ID, UNIQUE_ID)
        assert info.has_error is True
        assert "killed by OOM" in info.error_log

    def test_appends_without_truncating_existing(self, tmp_path, monkeypatch):
        from studio.app.common.core.snakemake import smk_status_logger as smk_mod
        from studio.app.common.core.snakemake.smk_status_logger import SmkStatusLogger

        monkeypatch.setattr(smk_mod.DIRPATH, "OUTPUT_DIR", str(tmp_path))

        SmkStatusLogger.record_external_error(WORKSPACE_ID, UNIQUE_ID, "first")
        SmkStatusLogger.record_external_error(WORKSPACE_ID, UNIQUE_ID, "second")

        info = SmkStatusLogger.get_error_content(WORKSPACE_ID, UNIQUE_ID)
        assert "first" in info.error_log
        assert "second" in info.error_log


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
