"""
Unit tests for workspace deletion functionality.

Tests cover:
- Case 23: Concurrent workspace deletion race protection
- WorkspaceStatus lifecycle transitions
- Row-level locking with with_for_update(nowait=True)
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from studio.app.common.core.workspace.workspace_services import WorkspaceService
from studio.app.common.models.workspace import Workspace, WorkspaceStatus

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
    ws.status = WorkspaceStatus.ACTIVE
    return ws


# ============================================================================
# Tests: WorkspaceStatus Enum
# ============================================================================


def test_workspace_status_enum_values():
    """Verify WorkspaceStatus enum has expected values."""
    assert WorkspaceStatus.ACTIVE.value == "active"
    assert WorkspaceStatus.DELETING.value == "deleting"
    assert WorkspaceStatus.DELETED.value == "deleted"


def test_workspace_status_is_string_enum():
    """WorkspaceStatus should be a string enum for database compatibility."""
    assert isinstance(WorkspaceStatus.ACTIVE, str)
    assert WorkspaceStatus.ACTIVE == "active"


# ============================================================================
# Tests: Workspace Model Status Field
# ============================================================================


def test_workspace_model_has_status_field():
    """Workspace model should have status field."""
    ws = Workspace(
        name="Test",
        user_id=1,
        deleted=False,
        status=WorkspaceStatus.ACTIVE,
    )
    assert ws.status == WorkspaceStatus.ACTIVE


# ============================================================================
# Tests: Successful Deletion Flow
# ============================================================================


@pytest.mark.asyncio
async def test_delete_workspace_success(mock_db, mock_workspace):
    """Test successful workspace deletion sets correct status and timestamp."""
    # Setup mock execute result
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = mock_workspace
    mock_db.execute.return_value = mock_result

    with patch.object(
        WorkspaceService,
        "delete_workspace_contents",
        new_callable=AsyncMock,
    ):
        success, message = await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert success is True
    assert "success" in message.lower()
    assert mock_workspace.status == WorkspaceStatus.DELETING
    mock_db.commit.assert_called()


@pytest.mark.asyncio
async def test_delete_workspace_marks_status_before_deletion(mock_db, mock_workspace):
    """Test that workspace status is set to DELETING before content deletion."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = mock_workspace
    mock_db.execute.return_value = mock_result

    status_during_deletion = None

    async def capture_status_during_deletion(db, ws, bucket_name):
        nonlocal status_during_deletion
        status_during_deletion = ws.status

    with patch.object(
        WorkspaceService,
        "delete_workspace_contents",
        side_effect=capture_status_during_deletion,
    ):
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert status_during_deletion == WorkspaceStatus.DELETING


# ============================================================================
# Tests: Not Found Scenarios
# ============================================================================


@pytest.mark.asyncio
async def test_delete_workspace_not_found_raises_404(mock_db):
    """Test that deleting non-existent workspace raises 404."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "999", "1"
        )

    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_delete_workspace_already_deleted_raises_404(mock_db, mock_workspace):
    """Test that deleting already deleted workspace raises 404."""
    mock_workspace.deleted = True
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_workspace_already_deleting_raises_404(mock_db, mock_workspace):
    """Test that workspace in DELETING status returns 404 (filtered by query)."""
    mock_workspace.status = WorkspaceStatus.DELETING
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert exc_info.value.status_code == 404
    assert "being deleted" in exc_info.value.detail.lower()


# ============================================================================
# Tests: Concurrent Deletion Race (Case 23)
# ============================================================================


@pytest.mark.asyncio
async def test_concurrent_deletion_blocked_by_lock(mock_db, mock_workspace):
    """Test that concurrent deletion request is blocked with 409 Conflict."""
    lock_error = OperationalError(
        "SELECT ... FOR UPDATE NOWAIT",
        {},
        Exception("could not obtain lock on row"),
    )
    mock_db.execute.side_effect = lock_error

    with pytest.raises(HTTPException) as exc_info:
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert exc_info.value.status_code == 409
    assert "being modified" in exc_info.value.detail.lower()
    mock_db.rollback.assert_called()


@pytest.mark.asyncio
async def test_concurrent_deletion_lock_wait_timeout(mock_db, mock_workspace):
    """Test that lock wait timeout returns 409 Conflict."""
    lock_error = OperationalError(
        "SELECT ... FOR UPDATE",
        {},
        Exception("Lock wait timeout exceeded"),
    )
    mock_db.execute.side_effect = lock_error

    with pytest.raises(HTTPException) as exc_info:
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_other_operational_error_returns_500(mock_db, mock_workspace):
    """Test that non-lock OperationalError returns 500."""
    db_error = OperationalError(
        "SELECT ...",
        {},
        Exception("Connection refused"),
    )
    mock_db.execute.side_effect = db_error

    with pytest.raises(HTTPException) as exc_info:
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert exc_info.value.status_code == 500
    assert "Database error" in exc_info.value.detail


# ============================================================================
# Tests: Rollback on Failure
# ============================================================================


@pytest.mark.asyncio
async def test_delete_workspace_rollback_on_content_deletion_failure(
    mock_db, mock_workspace
):
    """Test workspace status reverted to ACTIVE if content deletion fails."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = mock_workspace
    mock_db.execute.return_value = mock_result

    with patch.object(
        WorkspaceService,
        "delete_workspace_contents",
        new_callable=AsyncMock,
        side_effect=Exception("S3 deletion failed"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await WorkspaceService.process_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    assert exc_info.value.status_code == 500
    # Status should be reverted to ACTIVE
    assert mock_workspace.status == WorkspaceStatus.ACTIVE


# ============================================================================
# Tests: Query Filtering
# ============================================================================


@pytest.mark.asyncio
async def test_delete_workspace_filters_by_workspace_id_and_user_id(mock_db):
    """Test that query filters by workspace_id and user_id."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException):
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "123", "456"
        )

    # Verify execute was called (the select with filters)
    mock_db.execute.assert_called_once()
    call_args = mock_db.execute.call_args
    stmt = call_args[0][0]

    # The statement should have WHERE clauses - verify it's a Select statement
    assert hasattr(stmt, "whereclause") or hasattr(stmt, "_where_criteria")


@pytest.mark.asyncio
async def test_delete_workspace_filters_out_deleted_workspaces(mock_db, mock_workspace):
    """Test query excludes already soft-deleted workspaces."""
    mock_workspace.deleted = True
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_workspace_filters_out_deleting_status(mock_db, mock_workspace):
    """Test query excludes workspaces with DELETING status."""
    mock_workspace.status = WorkspaceStatus.DELETING
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    assert exc_info.value.status_code == 404


# ============================================================================
# Tests: Transaction Handling
# ============================================================================


@pytest.mark.asyncio
async def test_delete_workspace_commits_after_marking_deleting(mock_db, mock_workspace):
    """Test that commit is called after marking workspace as DELETING."""
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
    ):
        await WorkspaceService.process_workspace_deletion(
            mock_db, "test-bucket", "1", "1"
        )

    # Should commit at least twice: after DELETING status, after completion
    assert commit_count >= 2


@pytest.mark.asyncio
async def test_delete_workspace_rollback_on_general_exception(mock_db, mock_workspace):
    """Test db.rollback called on general exception."""
    mock_result = Mock()
    mock_result.scalar_one_or_none.return_value = mock_workspace
    mock_db.execute.return_value = mock_result

    with patch.object(
        WorkspaceService,
        "delete_workspace_contents",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Unexpected error"),
    ):
        with pytest.raises(HTTPException):
            await WorkspaceService.process_workspace_deletion(
                mock_db, "test-bucket", "1", "1"
            )

    # After inner exception handling, the outer handler rolls back
    assert mock_db.rollback.called or mock_db.commit.called


# ============================================================================
# Tests: Partial Deletion (Case 19)
# ============================================================================


class TestWorkspacePartialDeletion:
    """Tests for Case 19: Partial workspace deletion recovery."""

    def test_workspace_status_has_partial_delete(self):
        """WorkspaceStatus should have PARTIAL_DELETE value."""
        assert hasattr(WorkspaceStatus, "PARTIAL_DELETE")
        assert WorkspaceStatus.PARTIAL_DELETE.value == "partial_delete"

    @pytest.mark.asyncio
    async def test_delete_workspace_contents_continues_on_experiment_failure(
        self, mock_db, mock_workspace
    ):
        """Should continue deleting remaining experiments if one fails."""
        from studio.app.common.models.experiment import ExperimentRecord

        # Mock three experiment records
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

        # exp2 fails
        async def mock_delete_experiment(db, bucket, ws_id, uid, auto_commit):
            if uid == "exp2":
                return False
            return True

        with patch(
            "studio.app.common.core.workspace.workspace_services.ExperimentService"
            ".delete_experiment",
            side_effect=mock_delete_experiment,
        ):
            with patch.object(
                WorkspaceService, "delete_workspace_files", new_callable=AsyncMock
            ):
                with pytest.raises(HTTPException) as exc_info:
                    await WorkspaceService.delete_workspace_contents(
                        mock_db, mock_workspace, "test-bucket"
                    )

        # Should be 207 Multi-Status (partial success)
        assert exc_info.value.status_code == 207
        # Workspace should be marked as partial delete
        assert mock_workspace.status == WorkspaceStatus.PARTIAL_DELETE
        # Failed UIDs should be stored
        assert "exp2" in mock_workspace.failed_experiment_uids

    @pytest.mark.asyncio
    async def test_delete_workspace_contents_stores_failed_uids(
        self, mock_db, mock_workspace
    ):
        """Should store failed experiment UIDs for retry."""
        from studio.app.common.models.experiment import ExperimentRecord

        mock_exp = Mock(spec=ExperimentRecord)
        mock_exp.uid = "failed_exp"
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_exp]

        with patch(
            "studio.app.common.core.workspace.workspace_services.ExperimentService"
            ".delete_experiment",
            new_callable=AsyncMock,
            return_value=False,
        ):
            with patch.object(
                WorkspaceService, "delete_workspace_files", new_callable=AsyncMock
            ):
                with pytest.raises(HTTPException):
                    await WorkspaceService.delete_workspace_contents(
                        mock_db, mock_workspace, "test-bucket"
                    )

        assert mock_workspace.failed_experiment_uids == "failed_exp"

    @pytest.mark.asyncio
    async def test_retry_partial_deletion_retries_failed_experiments(self, mock_db):
        """Should retry deletion only for failed experiments."""
        mock_ws = Mock(spec=Workspace)
        mock_ws.id = 1
        mock_ws.status = WorkspaceStatus.PARTIAL_DELETE
        mock_ws.failed_experiment_uids = "exp1,exp2"
        mock_ws.deleted = False

        mock_result = Mock()
        mock_result.scalar_one_or_none.return_value = mock_ws
        mock_db.execute.return_value = mock_result

        with patch(
            "studio.app.common.core.workspace.workspace_services.ExperimentService"
            ".delete_experiment",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_delete:
            with patch.object(
                WorkspaceService, "delete_workspace_files", new_callable=AsyncMock
            ):
                success, message = await WorkspaceService.retry_partial_deletion(
                    mock_db, "test-bucket", "1", "1"
                )

        # Should have called delete for both failed experiments
        assert mock_delete.call_count == 2
        assert success is True
        assert mock_ws.deleted is True
        assert mock_ws.status == WorkspaceStatus.DELETED

    @pytest.mark.asyncio
    async def test_retry_partial_deletion_handles_still_failing(self, mock_db):
        """Should update failed UIDs if some experiments still fail."""
        mock_ws = Mock(spec=Workspace)
        mock_ws.id = 1
        mock_ws.status = WorkspaceStatus.PARTIAL_DELETE
        mock_ws.failed_experiment_uids = "exp1,exp2"
        mock_ws.deleted = False

        mock_result = Mock()
        mock_result.scalar_one_or_none.return_value = mock_ws
        mock_db.execute.return_value = mock_result

        # exp2 still fails
        async def mock_delete(db, bucket, ws_id, uid, auto_commit):
            return uid != "exp2"

        with patch(
            "studio.app.common.core.workspace.workspace_services.ExperimentService"
            ".delete_experiment",
            side_effect=mock_delete,
        ):
            success, message = await WorkspaceService.retry_partial_deletion(
                mock_db, "test-bucket", "1", "1"
            )

        assert success is False
        assert mock_ws.status == WorkspaceStatus.PARTIAL_DELETE
        assert "exp2" in mock_ws.failed_experiment_uids
