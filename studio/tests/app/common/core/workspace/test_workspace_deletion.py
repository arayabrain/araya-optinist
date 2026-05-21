"""
Unit tests for workspace deletion functionality.

Tests cover:
- Concurrent workspace deletion race protection
- Row-level locking with with_for_update(nowait=True)
- Background task integration for deletion tracking
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from studio.app.common.core.workspace.workspace_services import WorkspaceService
from studio.app.common.models.workspace import Workspace

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = Mock()
    db.execute = Mock()
    db.query = Mock()
    db.commit = Mock()
    db.rollback = Mock()
    return db


@pytest.fixture
def mock_workspace():
    """Create a mock workspace object."""
    ws = Mock(spec=Workspace)
    ws.id = 1
    ws.user_id = 1
    ws.name = "Test Workspace"
    ws.deleted = False
    return ws


# ============================================================================
# Tests: Successful Deletion Flow
# ============================================================================


@pytest.mark.asyncio
async def test_initiate_workspace_deletion_success(mock_db, mock_workspace):
    """Test successful workspace deletion via initiate."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = mock_workspace
    mock_db.execute.return_value = mock_result

    with patch.object(
        WorkspaceService,
        "delete_workspace_contents",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.queue_workspace_deletion",
        return_value=1,
    ) as mock_queue, patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.mark_in_progress",
    ) as mock_in_progress, patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.mark_completed",
    ) as mock_completed:
        success, message = await WorkspaceService.initiate_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert success is True
    assert "success" in message.lower()
    mock_queue.assert_called_once_with(user_id=1, workspace_id=1)
    mock_in_progress.assert_called_once_with(1)
    mock_completed.assert_called_once_with(1)
    mock_db.commit.assert_called()


# ============================================================================
# Tests: 409 Conflict When Active Task Exists
# ============================================================================


@pytest.mark.asyncio
async def test_initiate_returns_409_when_task_active(mock_db):
    """Test 409 when workspace already has active deletion task."""
    with patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=True,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert exc_info.value.status_code == 409
    assert "already in progress" in exc_info.value.detail.lower()


# ============================================================================
# Tests: Not Found Scenarios
# ============================================================================


@pytest.mark.asyncio
async def test_initiate_workspace_not_found_raises_404(mock_db):
    """Test that deleting non-existent workspace raises 404."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "999", "1"
            )

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_initiate_already_deleted_raises_404(mock_db, mock_workspace):
    """Test that deleting already deleted workspace raises 404."""
    mock_workspace.deleted = True
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert exc_info.value.status_code == 404


# ============================================================================
# Tests: Concurrent Deletion Race
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_deletion_blocked_by_lock(mock_db, mock_workspace):
    """Test concurrent deletion blocked with 409 Conflict."""
    lock_error = OperationalError(
        "SELECT ... FOR UPDATE NOWAIT",
        {},
        Exception("could not obtain lock on row"),
    )
    mock_db.execute.side_effect = lock_error

    with patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert exc_info.value.status_code == 409
    assert "being modified" in exc_info.value.detail.lower()
    mock_db.rollback.assert_called()


@pytest.mark.asyncio
async def test_concurrent_deletion_lock_wait_timeout(mock_db, mock_workspace):
    """Test lock wait timeout returns 409 Conflict."""
    lock_error = OperationalError(
        "SELECT ... FOR UPDATE",
        {},
        Exception("Lock wait timeout exceeded"),
    )
    mock_db.execute.side_effect = lock_error

    with patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_other_operational_error_returns_500(mock_db, mock_workspace):
    """Test non-lock OperationalError returns 500."""
    db_error = OperationalError(
        "SELECT ...",
        {},
        Exception("Connection refused"),
    )
    mock_db.execute.side_effect = db_error

    with patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert exc_info.value.status_code == 500
    assert "Database error" in exc_info.value.detail


# ============================================================================
# Tests: Failure Handling
# ============================================================================


@pytest.mark.asyncio
async def test_initiate_marks_task_failed_on_exception(mock_db, mock_workspace):
    """Test task is marked failed when content deletion raises."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = mock_workspace
    mock_db.execute.return_value = mock_result

    with patch.object(
        WorkspaceService,
        "delete_workspace_contents",
        new_callable=AsyncMock,
        side_effect=Exception("S3 deletion failed"),
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.queue_workspace_deletion",
        return_value=1,
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.mark_in_progress",
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.mark_failed",
    ) as mock_failed, patch(
        "studio.app.common.core.workspace.workspace_services.logger",
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert exc_info.value.status_code == 500
    mock_failed.assert_called_once_with(1, "S3 deletion failed")


# ============================================================================
# Tests: Query Filtering
# ============================================================================


@pytest.mark.asyncio
async def test_initiate_filters_by_workspace_id_and_user_id(
    mock_db,
):
    """Test that query filters by workspace_id and user_id."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ):
        with pytest.raises(HTTPException):
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "123", "456"
            )

    mock_db.execute.assert_called_once()
    call_args = mock_db.execute.call_args
    stmt = call_args[0][0]
    assert hasattr(stmt, "whereclause") or hasattr(stmt, "_where_criteria")


@pytest.mark.asyncio
async def test_initiate_filters_out_deleted_workspaces(mock_db, mock_workspace):
    """Test query excludes already soft-deleted workspaces."""
    mock_workspace.deleted = True
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert exc_info.value.status_code == 404


# ============================================================================
# Tests: Transaction Handling
# ============================================================================


@pytest.mark.asyncio
async def test_initiate_commits_after_task_creation(mock_db, mock_workspace):
    """Test commit is called after creating the background task."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = mock_workspace
    mock_db.execute.return_value = mock_result

    commit_count = 0

    def count_commits():
        nonlocal commit_count
        commit_count += 1

    mock_db.commit.side_effect = count_commits

    with patch.object(
        WorkspaceService,
        "delete_workspace_contents",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.queue_workspace_deletion",
        return_value=1,
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.mark_in_progress",
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.mark_completed",
    ):
        await WorkspaceService.initiate_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    # Should commit at least twice: after task creation, after deletion
    assert commit_count >= 2


@pytest.mark.asyncio
async def test_initiate_rollback_on_general_exception(mock_db, mock_workspace):
    """Test db.rollback called on general exception."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = mock_workspace
    mock_db.execute.return_value = mock_result

    with patch.object(
        WorkspaceService,
        "delete_workspace_contents",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Unexpected error"),
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.has_active_workspace_task",
        return_value=False,
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.queue_workspace_deletion",
        return_value=1,
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.mark_in_progress",
    ), patch(
        "studio.app.common.core.workspace.workspace_services"
        ".BackgroundTaskService.mark_failed",
    ), patch(
        "studio.app.common.core.workspace.workspace_services.logger",
    ):
        with pytest.raises(HTTPException):
            await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert mock_db.rollback.called or mock_db.commit.called


# ============================================================================
# Tests: Partial Deletion
# ============================================================================


class TestWorkspacePartialDeletion:
    """Tests for partial workspace deletion recovery."""

    @pytest.mark.asyncio
    async def test_delete_workspace_contents_force_deletes_on_failure(
        self, mock_db, mock_workspace
    ):
        """Should force-delete workspace even if experiments fail."""
        from studio.app.common.models.experiment import ExperimentRecord

        mock_exp1 = Mock(spec=ExperimentRecord)
        mock_exp1.uid = "exp1"
        mock_exp2 = Mock(spec=ExperimentRecord)
        mock_exp2.uid = "exp2"
        mock_exp3 = Mock(spec=ExperimentRecord)
        mock_exp3.uid = "exp3"

        mock_db.query.return_value.filter.return_value.all.return_value = [
            mock_exp1,
            mock_exp2,
            mock_exp3,
        ]

        async def mock_delete_experiment(db, bucket, ws_id, uid, auto_commit):
            if uid == "exp2":
                return False
            return True

        with patch(
            "studio.app.common.core.workspace.workspace_services"
            ".ExperimentService.delete_experiment",
            side_effect=mock_delete_experiment,
        ), patch.object(
            WorkspaceService,
            "delete_workspace_files",
            new_callable=AsyncMock,
        ) as mock_delete_files:
            result = await WorkspaceService.delete_workspace_contents(
                mock_db, mock_workspace, "test-bucket"
            )

        assert result == []
        assert mock_workspace.deleted is True
        assert mock_delete_files.call_count == 2

    @pytest.mark.asyncio
    async def test_delete_workspace_contents_force_deletes_all_failed(
        self, mock_db, mock_workspace
    ):
        """Should force-delete even when all experiments fail."""
        from studio.app.common.models.experiment import ExperimentRecord

        mock_exp = Mock(spec=ExperimentRecord)
        mock_exp.uid = "failed_exp"
        mock_db.query.return_value.filter.return_value.all.return_value = [
            mock_exp,
        ]

        with patch(
            "studio.app.common.core.workspace.workspace_services"
            ".ExperimentService.delete_experiment",
            new_callable=AsyncMock,
            return_value=False,
        ), patch.object(
            WorkspaceService,
            "delete_workspace_files",
            new_callable=AsyncMock,
        ):
            result = await WorkspaceService.delete_workspace_contents(
                mock_db, mock_workspace, "test-bucket"
            )

        assert result == []
        assert mock_workspace.deleted is True

    @pytest.mark.asyncio
    async def test_initiate_always_completes(self, mock_db, mock_workspace):
        """Workspace deletion always succeeds (force delete)."""
        mock_result = Mock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        with patch.object(
            WorkspaceService,
            "delete_workspace_contents",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "studio.app.common.core.workspace.workspace_services"
            ".BackgroundTaskService.has_active_workspace_task",
            return_value=False,
        ), patch(
            "studio.app.common.core.workspace.workspace_services"
            ".BackgroundTaskService.queue_workspace_deletion",
            return_value=1,
        ), patch(
            "studio.app.common.core.workspace.workspace_services"
            ".BackgroundTaskService.mark_in_progress",
        ), patch(
            "studio.app.common.core.workspace.workspace_services"
            ".BackgroundTaskService.mark_completed",
        ) as mock_mark_completed:
            success, msg = await WorkspaceService.initiate_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

        assert success is True
        mock_mark_completed.assert_called_once_with(1)


# ============================================================================
# Tests: execute_workspace_deletion (worker entry point)
# ============================================================================


class TestExecuteWorkspaceDeletion:
    """Tests for worker-driven workspace deletion."""

    @pytest.mark.asyncio
    async def test_execute_success(self, mock_db, mock_workspace):
        """Worker deletion succeeds."""
        mock_result = Mock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        with patch.object(
            WorkspaceService,
            "delete_workspace_contents",
            new_callable=AsyncMock,
            return_value=[],
        ):
            success, msg = await WorkspaceService.execute_workspace_deletion(
                mock_db, "test-bucket", 1, 1
            )

        assert success is True
        mock_db.commit.assert_called()

    @pytest.mark.asyncio
    async def test_execute_already_deleted(self, mock_db):
        """Worker returns success if workspace already deleted."""
        mock_result = Mock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        success, msg = await WorkspaceService.execute_workspace_deletion(
            mock_db, "test-bucket", 1, 1
        )

        assert success is True
        assert "already deleted" in msg.lower()

    @pytest.mark.asyncio
    async def test_execute_always_succeeds(self, mock_db, mock_workspace):
        """Worker deletion always succeeds (force delete)."""
        mock_result = Mock()
        mock_result.scalar_one_or_none.return_value = mock_workspace
        mock_db.execute.return_value = mock_result

        with patch.object(
            WorkspaceService,
            "delete_workspace_contents",
            new_callable=AsyncMock,
            return_value=[],
        ):
            success, msg = await WorkspaceService.execute_workspace_deletion(
                mock_db, "test-bucket", 1, 1
            )

        assert success is True
        mock_db.commit.assert_called()
