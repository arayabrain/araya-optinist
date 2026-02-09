"""
Tests for BackgroundTaskService (Case 18).
Verifies that background tasks are properly queued and processed.
"""
from unittest.mock import MagicMock, patch

from studio.app.common.models.experiment import (
    BackgroundTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)


class TestBackgroundTaskModel:
    """Tests for BackgroundTask model."""

    def test_background_task_status_enum(self):
        """Test BackgroundTaskStatus enum values."""
        assert BackgroundTaskStatus.QUEUED.value == "queued"
        assert BackgroundTaskStatus.IN_PROGRESS.value == "in_progress"
        assert BackgroundTaskStatus.COMPLETED.value == "completed"
        assert BackgroundTaskStatus.FAILED.value == "failed"
        assert BackgroundTaskStatus.RETRYING.value == "retrying"

    def test_background_task_type_enum(self):
        """Test BackgroundTaskType enum values."""
        assert BackgroundTaskType.EXPERIMENT.value == "experiment"
        assert BackgroundTaskType.WORKSPACE.value == "workspace"

    def test_background_task_model_fields(self):
        """Test BackgroundTask model has required fields."""
        assert hasattr(BackgroundTask, "user_id")
        assert hasattr(BackgroundTask, "task_type")
        assert hasattr(BackgroundTask, "resource_id")
        assert hasattr(BackgroundTask, "workspace_id")
        assert hasattr(BackgroundTask, "status")
        assert hasattr(BackgroundTask, "retry_count")
        assert hasattr(BackgroundTask, "max_retries")
        assert hasattr(BackgroundTask, "error_message")
        assert hasattr(BackgroundTask, "started_at")
        assert hasattr(BackgroundTask, "completed_at")


class TestBackgroundTaskServiceQueue:
    """Tests for BackgroundTaskService queueing methods."""

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_queue_experiment_deletion_creates_task(self, mock_session):
        """Should create a new background task for experiment."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result

        BackgroundTaskService.queue_experiment_deletion(
            user_id=1,
            workspace_id=100,
            experiment_uid="exp_123",
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_queue_experiment_deletion_returns_existing_task(self, mock_session):
        """Should return existing task ID if already queued."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        existing_task = MagicMock()
        existing_task.id = 42
        mock_result = MagicMock()
        mock_result.first.return_value = (existing_task,)
        mock_db.execute.return_value = mock_result

        task_id = BackgroundTaskService.queue_experiment_deletion(
            user_id=1,
            workspace_id=100,
            experiment_uid="exp_123",
        )

        assert task_id == 42
        mock_db.add.assert_not_called()

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_queue_workspace_deletion_creates_task(self, mock_session):
        """Should create a new background task for workspace."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result

        BackgroundTaskService.queue_workspace_deletion(
            user_id=1,
            workspace_id=100,
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()


class TestBackgroundTaskServiceStatus:
    """Tests for BackgroundTaskService status management."""

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_mark_in_progress(self, mock_session):
        """Should mark task as in progress."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_db.get.return_value = mock_task

        result = BackgroundTaskService.mark_in_progress(task_id=1)

        assert result is True
        assert mock_task.status == BackgroundTaskStatus.IN_PROGRESS.value
        mock_db.commit.assert_called_once()

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_mark_completed(self, mock_session):
        """Should mark task as completed."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_db.get.return_value = mock_task

        result = BackgroundTaskService.mark_completed(task_id=1)

        assert result is True
        assert mock_task.status == BackgroundTaskStatus.COMPLETED.value
        mock_db.commit.assert_called_once()

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_mark_failed_triggers_retry(self, mock_session):
        """Should mark task for retry if under max retries."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_task.retry_count = 0
        mock_task.max_retries = 3
        mock_db.get.return_value = mock_task

        result = BackgroundTaskService.mark_failed(
            task_id=1, error_message="Test error"
        )

        assert result is True
        assert mock_task.retry_count == 1
        assert mock_task.status == BackgroundTaskStatus.RETRYING.value

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_mark_failed_exhausts_retries(self, mock_session):
        """Should mark task as failed after max retries."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_task.retry_count = 2
        mock_task.max_retries = 3
        mock_db.get.return_value = mock_task

        result = BackgroundTaskService.mark_failed(
            task_id=1, error_message="Test error"
        )

        assert result is True
        assert mock_task.retry_count == 3
        assert mock_task.status == BackgroundTaskStatus.FAILED.value


class TestBackgroundTaskServicePending:
    """Tests for getting pending tasks."""

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_get_pending_tasks(self, mock_session):
        """Should return pending and retrying tasks."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_task = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_task,)]
        mock_db.execute.return_value = mock_result

        tasks = BackgroundTaskService.get_pending_tasks(limit=10)

        assert len(tasks) == 1

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_get_pending_tasks_empty(self, mock_session):
        """Should return empty list when no pending tasks."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_db.execute.return_value = mock_result

        tasks = BackgroundTaskService.get_pending_tasks(limit=10)

        assert tasks == []


class TestBackgroundTaskServiceCleanup:
    """Tests for cleanup of old tasks."""

    @patch("studio.app.common.core.experiment" ".background_task_service.session_scope")
    def test_cleanup_old_tasks(self, mock_session):
        """Should delete old completed/failed tasks."""
        from studio.app.common.core.experiment.background_task_service import (
            BackgroundTaskService,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_result = MagicMock()
        mock_result.rowcount = 10
        mock_db.execute.return_value = mock_result

        deleted = BackgroundTaskService.cleanup_old_tasks(days_old=30)

        assert deleted == 10
        mock_db.commit.assert_called_once()
