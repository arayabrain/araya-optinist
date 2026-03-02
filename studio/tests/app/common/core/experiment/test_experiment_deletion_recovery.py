"""
Tests for Experiment Deletion Recovery.

Tests the behavior when S3 deletion succeeds but DB deletion fails.
The experiment should be marked as orphaned rather than left as a ghost.

HOW TO RUN:
  cd studio/
  pytest tests/app/common/core/experiment/test_experiment_deletion_recovery.py -v
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

TEST_WORKSPACE_ID = "workspace_123"
TEST_UNIQUE_ID = "exp_456"
TEST_BUCKET_NAME = "test-bucket"


class TestExperimentDeletionRecovery:
    """Tests for S3 deletion succeeds but DB deletion fails."""

    @pytest.mark.asyncio
    async def test_successful_deletion(self):
        """Both S3 and DB deletion should succeed normally"""
        from studio.app.common.core.experiment.experiment_services import (
            ExperimentService,
        )

        mock_db = MagicMock()

        with patch(
            "studio.app.common.core.experiment.experiment_services."
            "RemoteStorageController.is_available",
            return_value=False,
        ):
            with patch(
                "studio.app.common.core.experiment."
                "experiment_services.ExptDataWriter"
            ) as mock_writer_class:
                mock_writer = MagicMock()
                mock_writer.delete_data = AsyncMock(return_value=True)
                mock_writer_class.return_value = mock_writer

                with patch(
                    "studio.app.common.core.experiment."
                    "experiment_services."
                    "ExperimentRecordService.is_available",
                    return_value=True,
                ):
                    with patch(
                        "studio.app.common.core.experiment."
                        "experiment_services."
                        "ExperimentRecordService.delete_record"
                    ) as mock_delete_record:
                        result = await ExperimentService.delete_experiment(
                            mock_db,
                            TEST_BUCKET_NAME,
                            TEST_WORKSPACE_ID,
                            TEST_UNIQUE_ID,
                        )

                        assert result is True
                        mock_delete_record.assert_called_once()

    @pytest.mark.asyncio
    async def test_db_failure_after_s3_marks_orphaned(self):
        """When DB deletion fails after S3, experiment is orphaned"""
        from studio.app.common.core.experiment.experiment_services import (
            ExperimentService,
        )

        mock_db = MagicMock()

        with patch(
            "studio.app.common.core.experiment.experiment_services."
            "RemoteStorageController.is_available",
            return_value=False,
        ):
            with patch(
                "studio.app.common.core.experiment."
                "experiment_services.ExptDataWriter"
            ) as mock_writer_class:
                mock_writer = MagicMock()
                mock_writer.delete_data = AsyncMock(return_value=True)
                mock_writer_class.return_value = mock_writer

                with patch(
                    "studio.app.common.core.experiment."
                    "experiment_services."
                    "ExperimentRecordService.is_available",
                    return_value=True,
                ):
                    with patch(
                        "studio.app.common.core.experiment."
                        "experiment_services."
                        "ExperimentRecordService.delete_record",
                        side_effect=Exception("DB connection lost"),
                    ):
                        with patch(
                            "studio.app.common.core.experiment."
                            "experiment_services."
                            "ExperimentRecordService."
                            "mark_as_orphaned"
                        ) as mock_mark_orphaned:
                            result = await ExperimentService.delete_experiment(
                                mock_db,
                                TEST_BUCKET_NAME,
                                TEST_WORKSPACE_ID,
                                TEST_UNIQUE_ID,
                            )

                            assert result is False
                            mock_mark_orphaned.assert_called_once()
                            call_args = mock_mark_orphaned.call_args
                            assert call_args[0][1] == TEST_WORKSPACE_ID
                            assert call_args[0][2] == TEST_UNIQUE_ID
                            assert "S3 data deleted" in call_args[1]["error_message"]

    @pytest.mark.asyncio
    async def test_s3_failure_does_not_mark_orphaned(self):
        """When S3 deletion also fails, should not mark orphaned"""
        from studio.app.common.core.experiment.experiment_services import (
            ExperimentService,
        )

        mock_db = MagicMock()

        with patch(
            "studio.app.common.core.experiment.experiment_services."
            "RemoteStorageController.is_available",
            return_value=False,
        ):
            with patch(
                "studio.app.common.core.experiment."
                "experiment_services.ExptDataWriter"
            ) as mock_writer_class:
                mock_writer = MagicMock()
                mock_writer.delete_data = AsyncMock(return_value=False)
                mock_writer_class.return_value = mock_writer

                with patch(
                    "studio.app.common.core.experiment."
                    "experiment_services."
                    "ExperimentRecordService.is_available",
                    return_value=True,
                ):
                    with patch(
                        "studio.app.common.core.experiment."
                        "experiment_services."
                        "ExperimentRecordService.delete_record",
                        side_effect=Exception("DB error"),
                    ):
                        with pytest.raises(Exception, match="DB error"):
                            await ExperimentService.delete_experiment(
                                mock_db,
                                TEST_BUCKET_NAME,
                                TEST_WORKSPACE_ID,
                                TEST_UNIQUE_ID,
                            )


class TestExperimentRecordMarkAsOrphaned:
    """Tests for ExperimentRecordService.mark_as_orphaned"""

    def test_mark_as_orphaned_sets_deletion_error(self):
        """mark_as_orphaned should set deletion_error field"""
        from studio.app.common.core.experiment.experiment_record_services import (
            ExperimentRecordService,
        )

        mock_db = MagicMock()
        mock_experiment = MagicMock()
        mock_db.query.return_value.filter.return_value.one.return_value = (
            mock_experiment
        )

        ExperimentRecordService.mark_as_orphaned(
            mock_db,
            TEST_WORKSPACE_ID,
            TEST_UNIQUE_ID,
            error_message="S3 data deleted, DB error",
        )

        assert mock_experiment.deletion_error == "S3 data deleted, DB error"

    def test_mark_as_orphaned_handles_missing_record(self):
        """mark_as_orphaned should not raise if record not found"""
        from sqlalchemy.exc import NoResultFound

        from studio.app.common.core.experiment.experiment_record_services import (
            ExperimentRecordService,
        )

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.one.side_effect = NoResultFound()

        ExperimentRecordService.mark_as_orphaned(
            mock_db,
            TEST_WORKSPACE_ID,
            TEST_UNIQUE_ID,
            error_message="S3 data deleted",
        )
