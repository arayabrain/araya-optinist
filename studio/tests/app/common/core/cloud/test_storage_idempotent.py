"""
Tests for idempotent storage operations (Cases 16-17).
Verifies that storage increment/decrement operations are idempotent
and prevent double-counting.
"""
from unittest.mock import MagicMock, patch

from studio.app.common.models.subscription import (
    StorageOperation,
    StorageOperationStatus,
    StorageOperationType,
)


class TestStorageOperationModel:
    """Tests for StorageOperation model."""

    def test_storage_operation_status_enum(self):
        """Test StorageOperationStatus enum values."""
        assert StorageOperationStatus.PENDING.value == "pending"
        assert StorageOperationStatus.COMPLETED.value == "completed"
        assert StorageOperationStatus.FAILED.value == "failed"

    def test_storage_operation_type_enum(self):
        """Test StorageOperationType enum values."""
        assert StorageOperationType.INCREMENT.value == "increment"
        assert StorageOperationType.DECREMENT.value == "decrement"

    def test_storage_operation_model_fields(self):
        """Test StorageOperation model has required fields."""
        # Verify model has expected attributes
        assert hasattr(StorageOperation, "user_id")
        assert hasattr(StorageOperation, "idempotency_key")
        assert hasattr(StorageOperation, "operation_type")
        assert hasattr(StorageOperation, "bytes_delta")
        assert hasattr(StorageOperation, "status")
        assert hasattr(StorageOperation, "error_message")
        assert hasattr(StorageOperation, "created_at")
        assert hasattr(StorageOperation, "completed_at")


class TestIdempotentStorageIncrement:
    """Tests for increment_storage_idempotent function."""

    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    @patch("studio.app.common.core.cloud.cloud_utils.increment_user_storage")
    def test_idempotent_increment_prevents_double_count(
        self, mock_increment, mock_session
    ):
        """Same idempotency key should not increment twice."""
        from studio.app.common.core.cloud.cloud_utils import (
            increment_storage_idempotent,
        )

        # Mock existing completed operation
        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # First call - operation exists and is completed
        mock_result = MagicMock()
        mock_result.first.return_value = MagicMock()  # Existing completed operation
        mock_db.execute.return_value = mock_result

        result = increment_storage_idempotent(
            user_id=1,
            bytes_delta=1000,
            idempotency_key="upload_123",
        )

        assert result is True
        # Should not call actual increment since already completed
        mock_increment.assert_not_called()

    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    @patch("studio.app.common.core.cloud.cloud_utils.increment_user_storage")
    def test_idempotent_increment_creates_new_operation(
        self, mock_increment, mock_session
    ):
        """New idempotency key should create operation and increment."""
        from studio.app.common.core.cloud.cloud_utils import (
            increment_storage_idempotent,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # No existing operation
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_db.execute.return_value = mock_result
        mock_increment.return_value = True

        # Mock the operation object
        mock_db.get.return_value = MagicMock()

        increment_storage_idempotent(
            user_id=1,
            bytes_delta=1000,
            idempotency_key="upload_new",
        )

        # Should have called increment
        mock_increment.assert_called_once_with(1, 1000)

    def test_idempotent_increment_skips_zero_bytes(self):
        """Zero bytes should return True without doing anything."""
        from studio.app.common.core.cloud.cloud_utils import (
            increment_storage_idempotent,
        )

        result = increment_storage_idempotent(
            user_id=1,
            bytes_delta=0,
            idempotency_key="upload_zero",
        )

        assert result is True

    def test_idempotent_increment_skips_negative_bytes(self):
        """Negative bytes should return True without doing anything."""
        from studio.app.common.core.cloud.cloud_utils import (
            increment_storage_idempotent,
        )

        result = increment_storage_idempotent(
            user_id=1,
            bytes_delta=-100,
            idempotency_key="upload_negative",
        )

        assert result is True


class TestIdempotentStorageDecrement:
    """Tests for decrement_storage_idempotent function."""

    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    @patch("studio.app.common.core.cloud.cloud_utils.decrement_user_storage")
    def test_idempotent_decrement_prevents_double_subtraction(
        self, mock_decrement, mock_session
    ):
        """Same idempotency key should not decrement twice."""
        from studio.app.common.core.cloud.cloud_utils import (
            decrement_storage_idempotent,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        # Existing completed operation
        mock_result = MagicMock()
        mock_result.first.return_value = MagicMock()
        mock_db.execute.return_value = mock_result

        result = decrement_storage_idempotent(
            user_id=1,
            bytes_delta=500,
            idempotency_key="delete_123",
        )

        assert result is True
        mock_decrement.assert_not_called()

    def test_idempotent_decrement_skips_zero_bytes(self):
        """Zero bytes should return True without doing anything."""
        from studio.app.common.core.cloud.cloud_utils import (
            decrement_storage_idempotent,
        )

        result = decrement_storage_idempotent(
            user_id=1,
            bytes_delta=0,
            idempotency_key="delete_zero",
        )

        assert result is True


class TestStorageOperationCleanup:
    """Tests for cleanup_old_storage_operations function."""

    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_cleanup_deletes_old_completed_operations(self, mock_session):
        """Should delete completed operations older than specified days."""
        from studio.app.common.core.cloud.cloud_utils import (
            cleanup_old_storage_operations,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_db.execute.return_value = mock_result

        deleted = cleanup_old_storage_operations(days_old=7)

        assert deleted == 5
        mock_db.commit.assert_called_once()

    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_cleanup_handles_errors(self, mock_session):
        """Should return 0 and not raise on errors."""
        from studio.app.common.core.cloud.cloud_utils import (
            cleanup_old_storage_operations,
        )

        mock_session.return_value.__enter__.side_effect = Exception("DB error")

        deleted = cleanup_old_storage_operations(days_old=7)

        assert deleted == 0


class TestGetPendingStorageOperations:
    """Tests for get_pending_storage_operations function."""

    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_returns_pending_operations(self, mock_session):
        """Should return list of pending operations for user."""
        from studio.app.common.core.cloud.cloud_utils import (
            get_pending_storage_operations,
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db

        mock_op = MagicMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_op,)]
        mock_db.execute.return_value = mock_result

        operations = get_pending_storage_operations(user_id=1)

        assert len(operations) == 1

    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_returns_empty_list_on_error(self, mock_session):
        """Should return empty list on errors."""
        from studio.app.common.core.cloud.cloud_utils import (
            get_pending_storage_operations,
        )

        mock_session.return_value.__enter__.side_effect = Exception("DB error")

        operations = get_pending_storage_operations(user_id=1)

        assert operations == []


class TestProcessStalePendingOperations:
    """Tests for process_stale_pending_operations function (Case 69)."""

    @patch("studio.app.common.core.cloud.cloud_utils.increment_user_storage")
    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_retries_stale_increment_operations(self, mock_session, mock_increment):
        """Should retry stale pending increment operations."""
        from datetime import datetime, timedelta, timezone

        from studio.app.common.core.cloud.cloud_utils import (
            STALE_PENDING_THRESHOLD_MINUTES,
            process_stale_pending_operations,
        )

        # Create mock stale operation
        mock_op = MagicMock()
        mock_op.idempotency_key = "test_key"
        mock_op.operation_type = "increment"
        mock_op.user_id = 1
        mock_op.bytes_delta = 1000
        mock_op.retry_count = 0
        mock_op.created_at = datetime.now(timezone.utc) - timedelta(
            minutes=STALE_PENDING_THRESHOLD_MINUTES + 5
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.execute.return_value.all.return_value = [(mock_op,)]
        mock_increment.return_value = True

        result = process_stale_pending_operations()

        assert result["processed"] == 1
        assert result["succeeded"] == 1
        assert result["failed"] == 0
        mock_increment.assert_called_once_with(1, 1000)
        assert mock_op.status == "completed"

    @patch("studio.app.common.core.cloud.cloud_utils.decrement_user_storage")
    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_retries_stale_decrement_operations(self, mock_session, mock_decrement):
        """Should retry stale pending decrement operations."""
        from datetime import datetime, timedelta, timezone

        from studio.app.common.core.cloud.cloud_utils import (
            STALE_PENDING_THRESHOLD_MINUTES,
            process_stale_pending_operations,
        )

        mock_op = MagicMock()
        mock_op.idempotency_key = "test_key"
        mock_op.operation_type = "decrement"
        mock_op.user_id = 1
        mock_op.bytes_delta = 500
        mock_op.retry_count = 0
        mock_op.created_at = datetime.now(timezone.utc) - timedelta(
            minutes=STALE_PENDING_THRESHOLD_MINUTES + 5
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.execute.return_value.all.return_value = [(mock_op,)]
        mock_decrement.return_value = True

        result = process_stale_pending_operations()

        assert result["processed"] == 1
        assert result["succeeded"] == 1
        mock_decrement.assert_called_once_with(1, 500)

    @patch("studio.app.common.core.cloud.cloud_utils.increment_user_storage")
    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_marks_failed_after_max_retries(self, mock_session, mock_increment):
        """Should mark operation as failed after exceeding max retries."""
        from datetime import datetime, timedelta, timezone

        from studio.app.common.core.cloud.cloud_utils import (
            STALE_PENDING_THRESHOLD_MINUTES,
            process_stale_pending_operations,
        )

        mock_op = MagicMock()
        mock_op.idempotency_key = "test_key"
        mock_op.operation_type = "increment"
        mock_op.user_id = 1
        mock_op.bytes_delta = 1000
        mock_op.retry_count = 5  # Already at max
        mock_op.created_at = datetime.now(timezone.utc) - timedelta(
            minutes=STALE_PENDING_THRESHOLD_MINUTES + 5
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.execute.return_value.all.return_value = [(mock_op,)]

        result = process_stale_pending_operations(max_retries=3)

        assert result["processed"] == 1
        assert result["failed"] == 1
        assert result["succeeded"] == 0
        mock_increment.assert_not_called()
        assert mock_op.status == "failed"

    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_returns_empty_result_on_error(self, mock_session):
        """Should return empty result dict on errors."""
        from studio.app.common.core.cloud.cloud_utils import (
            process_stale_pending_operations,
        )

        mock_session.return_value.__enter__.side_effect = Exception("DB error")

        result = process_stale_pending_operations()

        assert result == {"processed": 0, "succeeded": 0, "failed": 0}

    @patch("studio.app.common.core.cloud.cloud_utils.increment_user_storage")
    @patch("studio.app.common.core.cloud.cloud_utils.session_scope")
    def test_handles_operation_failure_gracefully(self, mock_session, mock_increment):
        """Should handle individual operation failures gracefully."""
        from datetime import datetime, timedelta, timezone

        from studio.app.common.core.cloud.cloud_utils import (
            STALE_PENDING_THRESHOLD_MINUTES,
            process_stale_pending_operations,
        )

        mock_op = MagicMock()
        mock_op.idempotency_key = "test_key"
        mock_op.operation_type = "increment"
        mock_op.user_id = 1
        mock_op.bytes_delta = 1000
        mock_op.retry_count = 0
        mock_op.created_at = datetime.now(timezone.utc) - timedelta(
            minutes=STALE_PENDING_THRESHOLD_MINUTES + 5
        )

        mock_db = MagicMock()
        mock_session.return_value.__enter__.return_value = mock_db
        mock_db.execute.return_value.all.return_value = [(mock_op,)]
        mock_increment.side_effect = Exception("Operation failed")

        result = process_stale_pending_operations()

        assert result["processed"] == 1
        assert result["failed"] == 1
        assert "Operation failed" in mock_op.error_message
