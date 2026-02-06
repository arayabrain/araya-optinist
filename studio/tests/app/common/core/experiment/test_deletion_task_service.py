"""
Tests for DeletionTaskService (Case 18).
Verifies that deletion tasks are properly queued and processed.
"""
from unittest.mock import MagicMock, patch

from studio.app.common.models.experiment import (
    DeletionTask,
    DeletionTaskStatus,
    DeletionTaskType,
)


class TestDeletionTaskModel:
    """Tests for DeletionTask model."""

    def test_deletion_task_status_enum(self):
        """Test DeletionTaskStatus enum values."""
        assert DeletionTaskStatus.QUEUED.value == "queued"
        assert DeletionTaskStatus.IN_PROGRESS.value == "in_progress"
        assert DeletionTaskStatus.COMPLETED.value == "completed"
        assert DeletionTaskStatus.FAILED.value == "failed"
        assert DeletionTaskStatus.RETRYING.value == "retrying"

    def test_deletion_task_type_enum(self):
        """Test DeletionTaskType enum values."""
        assert DeletionTaskType.EXPERIMENT.value == "experiment"
        assert DeletionTaskType.WORKSPACE.value == "workspace"

    def test_deletion_task_model_fields(self):
        """Test DeletionTask model has required fields."""
        assert hasattr(DeletionTask, "user_id")
        assert hasattr(DeletionTask, "task_type")
        assert hasattr(DeletionTask, "resource_id")
        assert hasattr(DeletionTask, "workspace_id")
        assert hasattr(DeletionTask, "status")
        assert hasattr(DeletionTask, "retry_count")
        assert hasattr(DeletionTask, "max_retries")
        assert hasattr(DeletionTask, "error_message")
        assert hasattr(DeletionTask, "started_at")
        assert hasattr(DeletionTask, "completed_at")


class TestDeletionTaskServiceQueue:
    """Tests for DeletionTaskService queueing methods."""

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_queue_experiment_deletion_creates_task(self, mock_session):
        """Should create a new deletion task for experiment."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # No existing task
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result

        DeletionTaskService.queue_experiment_deletion(
            user_id=1,
            workspace_id=100,
            experiment_uid="exp_123",
        )

        # Should have added a task
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_queue_experiment_deletion_returns_existing_task(self, mock_session):
        """Should return existing task ID if already queued."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # Existing task
        existing_task = MagicMock()
        existing_task.id = 42
        mock_result = MagicMock()
        mock_result.first.return_value = (existing_task,)
        mock_db.execute.return_value = mock_result

        task_id = DeletionTaskService.queue_experiment_deletion(
            user_id=1,
            workspace_id=100,
            experiment_uid="exp_123",
        )

        assert task_id == 42
        mock_db.add.assert_not_called()

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_queue_workspace_deletion_creates_task(self, mock_session):
        """Should create a new deletion task for workspace."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result

        DeletionTaskService.queue_workspace_deletion(
            user_id=1,
            workspace_id=100,
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestDeletionTaskServiceStatus:
    """Tests for DeletionTaskService status management."""

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_mark_in_progress(self, mock_session):
        """Should mark task as in progress."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_db.get.return_value = mock_task

        result = DeletionTaskService.mark_in_progress(task_id=1)

        assert result is True
        assert mock_task.status == DeletionTaskStatus.IN_PROGRESS.value
        mock_db.commit.assert_called_once()

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_mark_completed(self, mock_session):
        """Should mark task as completed."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_db.get.return_value = mock_task

        result = DeletionTaskService.mark_completed(task_id=1)

        assert result is True
        assert mock_task.status == DeletionTaskStatus.COMPLETED.value
        mock_db.commit.assert_called_once()

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_mark_failed_triggers_retry(self, mock_session):
        """Should mark task for retry if under max retries."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_task.retry_count = 0
        mock_task.max_retries = 3
        mock_db.get.return_value = mock_task

        result = DeletionTaskService.mark_failed(task_id=1, error_message="Test error")

        assert result is True
        assert mock_task.retry_count == 1
        assert mock_task.status == DeletionTaskStatus.RETRYING.value

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_mark_failed_exhausts_retries(self, mock_session):
        """Should mark task as failed after max retries."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_task.retry_count = 2
        mock_task.max_retries = 3
        mock_db.get.return_value = mock_task

        result = DeletionTaskService.mark_failed(task_id=1, error_message="Test error")

        assert result is True
        assert mock_task.retry_count == 3
        assert mock_task.status == DeletionTaskStatus.FAILED.value


class TestDeletionTaskServicePending:
    """Tests for getting pending tasks."""

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_get_pending_tasks(self, mock_session):
        """Should return pending and retrying tasks."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_task,)]
        mock_db.execute.return_value = mock_result

        tasks = DeletionTaskService.get_pending_tasks(limit=10)

        assert len(tasks) == 1

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_get_pending_tasks_empty(self, mock_session):
        """Should return empty list when no pending tasks."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        tasks = DeletionTaskService.get_pending_tasks(limit=10)

        assert tasks == []


class TestDeletionTaskServiceCleanup:
    """Tests for cleanup of old tasks."""

    @patch("studio.app.common.core.experiment.deletion_task_service.session_scope")
    def test_cleanup_old_tasks(self, mock_session):
        """Should delete old completed/failed tasks."""
        from studio.app.common.core.experiment.deletion_task_service import (
            DeletionTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_result = MagicMock()
        mock_result.rowcount = 10
        mock_db.execute.return_value = mock_result

        deleted = DeletionTaskService.cleanup_old_tasks(days_old=30)

        assert deleted == 10
        mock_db.commit.assert_called_once()
