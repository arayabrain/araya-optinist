"""
Unit tests for the post-process logic in _snakemake_execute_process.

These tests verify that experiment record registration and data usage
updates are skipped when observe_overall() fails, and that a warning
is logged.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

MODULE = "studio.app.common.core.snakemake.snakemake_executor"

WORKSPACE_ID = "test_workspace"
UNIQUE_ID = "test_unique_id"


@pytest.fixture()
def _patch_snakemake_execution():
    """Patch everything except the post-process block under test."""
    with (
        patch(f"{MODULE}.SmkStatusLogger"),
        patch(f"{MODULE}.join_filepath", return_value="/tmp/fake"),
        patch(f"{MODULE}.SnakemakeApi") as mock_api_cls,
        patch(f"{MODULE}.RemoteStorageController") as mock_remote,
        patch(f"{MODULE}.RemoteSyncLockFileUtil"),
        patch(f"{MODULE}.RemoteSyncStatusFileUtil"),
        patch(f"{MODULE}.get_pickle_file"),
        patch(f"{MODULE}.DIRPATH"),
    ):
        # Make snakemake execution itself succeed
        mock_ctx = MagicMock()
        mock_api_cls.return_value.__enter__ = MagicMock(return_value=mock_ctx)
        mock_api_cls.return_value.__exit__ = MagicMock(return_value=False)
        dag_mock = MagicMock()
        mock_ctx.dag.return_value.__enter__ = MagicMock(return_value=dag_mock)
        mock_ctx.dag.return_value.__exit__ = MagicMock(return_value=False)

        mock_remote.is_available.return_value = False
        yield


@pytest.fixture()
def mock_observe():
    """Patch WorkflowResult and its observe_overall method."""
    with patch(f"{MODULE}.WorkflowResult") as mock_cls:
        mock_instance = MagicMock()
        mock_cls.return_value = mock_instance
        mock_instance.observe_overall = AsyncMock()
        yield mock_instance


@pytest.fixture()
def mock_experiment_record():
    with patch(f"{MODULE}.ExperimentRecordService") as mock_svc:
        mock_svc.is_available.return_value = True
        yield mock_svc


@pytest.fixture()
def mock_data_capacity():
    with patch(f"{MODULE}.WorkspaceDataCapacityService") as mock_svc:
        yield mock_svc


class TestPostProcessObserveSuccess:
    """When observe_overall() succeeds, downstream services should run."""

    @pytest.mark.usefixtures("_patch_snakemake_execution")
    def test_calls_record_and_data_usage(
        self, mock_observe, mock_experiment_record, mock_data_capacity
    ):
        from studio.app.common.core.snakemake.snakemake_executor import (
            _snakemake_execute_process,
        )

        _snakemake_execute_process(WORKSPACE_ID, UNIQUE_ID, MagicMock())

        record_fn = mock_experiment_record.regist_record_on_workflow_completed
        record_fn.assert_called_once_with(WORKSPACE_ID, UNIQUE_ID)
        mock_data_capacity.update_experiment_data_usage.assert_called_once_with(
            WORKSPACE_ID, UNIQUE_ID
        )


class TestPostProcessObserveFailure:
    """When observe_overall() fails, downstream services should be skipped."""

    @pytest.fixture(autouse=True)
    def _fail_observe(self, mock_observe):
        mock_observe.observe_overall = AsyncMock(
            side_effect=RuntimeError("observe failed")
        )

    @pytest.mark.usefixtures("_patch_snakemake_execution")
    def test_skips_record_and_data_usage(
        self, mock_observe, mock_experiment_record, mock_data_capacity
    ):
        from studio.app.common.core.snakemake.snakemake_executor import (
            _snakemake_execute_process,
        )

        _snakemake_execute_process(WORKSPACE_ID, UNIQUE_ID, MagicMock())

        mock_experiment_record.regist_record_on_workflow_completed.assert_not_called()
        mock_data_capacity.update_experiment_data_usage.assert_not_called()

    @pytest.mark.usefixtures("_patch_snakemake_execution")
    def test_logs_warning(
        self, mock_observe, mock_experiment_record, mock_data_capacity, caplog
    ):
        from studio.app.common.core.snakemake.snakemake_executor import (
            _snakemake_execute_process,
        )

        with caplog.at_level(logging.WARNING):
            _snakemake_execute_process(WORKSPACE_ID, UNIQUE_ID, MagicMock())

        assert any(
            "Skipped experiment record registration and data usage update"
            in record.message
            and WORKSPACE_ID in record.message
            and UNIQUE_ID in record.message
            for record in caplog.records
        )
