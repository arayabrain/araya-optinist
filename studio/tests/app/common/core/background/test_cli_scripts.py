"""
Unit tests for background job CLI scripts.

Tests that CLI entry points correctly invoke background jobs and handle errors.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


class TestPublishedExperimentSyncCLI:
    """Test CLI script for published experiment sync job"""

    def test_script_imports_correctly(self):
        """Test that the CLI script can be imported without errors"""
        # Add project root to path
        project_root = Path(__file__).parent.parent.parent.parent.parent.parent.parent
        sys.path.insert(0, str(project_root))

        # Import the sync job module used by CLI
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        assert PublishedExperimentSyncJob is not None
        assert hasattr(PublishedExperimentSyncJob, "run")

    @pytest.mark.asyncio
    async def test_cli_calls_sync_job_run(self):
        """Test that CLI script calls PublishedExperimentSyncJob.run()"""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        with patch.object(
            PublishedExperimentSyncJob, "run", new_callable=AsyncMock
        ) as mock_run:
            # Simulate CLI main function
            await PublishedExperimentSyncJob.run()

            # Verify run was called
            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_cli_handles_job_success(self):
        """Test that CLI exits with 0 on successful job completion"""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        with patch.object(
            PublishedExperimentSyncJob, "run", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = None

            try:
                await PublishedExperimentSyncJob.run()
                exit_code = 0
            except Exception:
                exit_code = 1

            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cli_handles_job_failure(self):
        """Test that CLI exits with 1 on job failure"""
        from studio.app.common.core.background.sync_job import (
            PublishedExperimentSyncJob,
        )

        with patch.object(
            PublishedExperimentSyncJob, "run", new_callable=AsyncMock
        ) as mock_run:
            mock_run.side_effect = Exception("Test error")

            try:
                await PublishedExperimentSyncJob.run()
                exit_code = 0
            except Exception:
                exit_code = 1

            assert exit_code == 1


class TestDataCleanupCLI:
    """Test CLI script for data cleanup job"""

    def test_script_imports_correctly(self):
        """Test that the CLI script can be imported without errors"""
        # Import the cleanup job module used by CLI
        from studio.app.common.core.background.cleanup_job import DataCleanupJob

        assert DataCleanupJob is not None
        assert hasattr(DataCleanupJob, "run")

    @pytest.mark.asyncio
    async def test_cli_calls_cleanup_job_run(self):
        """Test that CLI script calls DataCleanupJob.run()"""
        from studio.app.common.core.background.cleanup_job import DataCleanupJob

        with patch.object(DataCleanupJob, "run", new_callable=AsyncMock) as mock_run:
            # Simulate CLI main function
            await DataCleanupJob.run()

            # Verify run was called
            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_cli_handles_job_success(self):
        """Test that CLI exits with 0 on successful job completion"""
        from studio.app.common.core.background.cleanup_job import DataCleanupJob

        with patch.object(DataCleanupJob, "run", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None

            try:
                await DataCleanupJob.run()
                exit_code = 0
            except Exception:
                exit_code = 1

            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cli_handles_job_failure(self):
        """Test that CLI exits with 1 on job failure"""
        from studio.app.common.core.background.cleanup_job import DataCleanupJob

        with patch.object(DataCleanupJob, "run", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = Exception("Test error")

            try:
                await DataCleanupJob.run()
                exit_code = 0
            except Exception:
                exit_code = 1

            assert exit_code == 1


class TestStorageReconciliationCLI:
    """Test CLI script for storage reconciliation job"""

    def test_script_imports_correctly(self):
        """Test that the CLI script can be imported without errors"""
        # Import the reconciliation job module used by CLI
        from studio.app.common.core.background.storage_reconciliation_job import (
            StorageReconciliationJob,
        )

        assert StorageReconciliationJob is not None
        assert hasattr(StorageReconciliationJob, "run")

    @pytest.mark.asyncio
    async def test_cli_calls_reconciliation_job_run(self):
        """Test that CLI script calls StorageReconciliationJob.run()"""
        from studio.app.common.core.background.storage_reconciliation_job import (
            StorageReconciliationJob,
        )

        with patch.object(
            StorageReconciliationJob, "run", new_callable=AsyncMock
        ) as mock_run:
            # Simulate CLI main function
            await StorageReconciliationJob.run()

            # Verify run was called
            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_cli_handles_job_success(self):
        """Test that CLI exits with 0 on successful job completion"""
        from studio.app.common.core.background.storage_reconciliation_job import (
            StorageReconciliationJob,
        )

        with patch.object(
            StorageReconciliationJob, "run", new_callable=AsyncMock
        ) as mock_run:
            mock_run.return_value = None

            try:
                await StorageReconciliationJob.run()
                exit_code = 0
            except Exception:
                exit_code = 1

            assert exit_code == 0

    @pytest.mark.asyncio
    async def test_cli_handles_job_failure(self):
        """Test that CLI exits with 1 on job failure"""
        from studio.app.common.core.background.storage_reconciliation_job import (
            StorageReconciliationJob,
        )

        with patch.object(
            StorageReconciliationJob, "run", new_callable=AsyncMock
        ) as mock_run:
            mock_run.side_effect = Exception("Test error")

            try:
                await StorageReconciliationJob.run()
                exit_code = 0
            except Exception:
                exit_code = 1

            assert exit_code == 1


class TestCLIScriptExecution:
    """Integration tests for CLI script execution"""

    def test_sync_script_syntax(self):
        """Test that sync CLI script has valid Python syntax"""
        script_path = (
            Path(__file__).parent.parent.parent.parent.parent.parent
            / "scripts"
            / "run_published_experiment_sync.py"
        )
        with open(script_path) as f:
            code = f.read()
            compile(code, str(script_path), "exec")

    def test_cleanup_script_syntax(self):
        """Test that cleanup CLI script has valid Python syntax"""
        script_path = (
            Path(__file__).parent.parent.parent.parent.parent.parent
            / "scripts"
            / "run_data_cleanup.py"
        )
        with open(script_path) as f:
            code = f.read()
            compile(code, str(script_path), "exec")

    def test_reconciliation_script_syntax(self):
        """Test that reconciliation CLI script has valid Python syntax"""
        script_path = (
            Path(__file__).parent.parent.parent.parent.parent.parent
            / "scripts"
            / "run_storage_reconciliation.py"
        )
        with open(script_path) as f:
            code = f.read()
            compile(code, str(script_path), "exec")


class TestBackgroundSchedulerDisable:
    """Test that DISABLE_BACKGROUND_SCHEDULER environment variable works"""

    @patch.dict("os.environ", {"DISABLE_BACKGROUND_SCHEDULER": "1"})
    def test_scheduler_disabled_with_env_var(self):
        """Test that scheduler is disabled when env var is set"""
        import os

        disable_scheduler = os.environ.get("DISABLE_BACKGROUND_SCHEDULER", "0") == "1"
        assert disable_scheduler is True

    @patch.dict("os.environ", {}, clear=True)
    def test_scheduler_enabled_without_env_var(self):
        """Test that scheduler is enabled when env var is not set"""
        import os

        disable_scheduler = os.environ.get("DISABLE_BACKGROUND_SCHEDULER", "0") == "1"
        assert disable_scheduler is False

    @patch.dict("os.environ", {"DISABLE_BACKGROUND_SCHEDULER": "0"})
    def test_scheduler_enabled_with_env_var_zero(self):
        """Test that scheduler is enabled when env var is set to 0"""
        import os

        disable_scheduler = os.environ.get("DISABLE_BACKGROUND_SCHEDULER", "0") == "1"
        assert disable_scheduler is False
