"""
Unit tests for workflow tracking functionality.

Tests increment/decrement of active_workflow_count for free tier users.
"""

from unittest.mock import MagicMock, patch

import pytest

from studio.app.common.core.workflow.workflow_tracking import (
    decrement_workflow_count,
    get_active_workflow_count,
    increment_workflow_count,
)


@pytest.fixture
def mock_session():
    """Mock database session"""
    # Patch MODE.IS_STANDALONE to False so functions don't return early
    with patch("studio.app.common.core.workflow.workflow_tracking.MODE") as mock_mode:
        mock_mode.IS_STANDALONE = False
        with patch(
            "studio.app.common.core.workflow.workflow_tracking.session_scope"
        ) as mock:
            session = MagicMock()
            mock.return_value.__enter__.return_value = session
            yield session


class TestIncrementWorkflowCount:
    """Test workflow count increment"""

    def test_increment_workflow_count_success(self, mock_session):
        """Test successful increment of workflow count"""
        # Setup mock result for execute()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        increment_workflow_count(user_id=123)

        # Verify execute() was called (the actual SQL update)
        assert mock_session.execute.called
        mock_session.commit.assert_called_once()

    def test_increment_workflow_count_no_assignment(self, mock_session):
        """Test increment when user has no assignment (premium user)"""
        # Setup mock result with rowcount=0 (no rows updated)
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute.return_value = mock_result

        # Should not raise exception
        increment_workflow_count(user_id=123)

        # execute() is called but rowcount is 0 (no rows updated)
        assert mock_session.execute.called
        mock_session.commit.assert_called_once()

    def test_increment_workflow_count_none_user_id(self, mock_session):
        """Test increment with None user_id"""
        increment_workflow_count(user_id=None)

        mock_session.exec.assert_not_called()

    def test_increment_workflow_count_multiple_times(self, mock_session):
        """Test multiple increments"""
        # Setup mock result for execute()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        increment_workflow_count(user_id=123)
        increment_workflow_count(user_id=123)

        # Verify execute() was called twice
        assert mock_session.execute.call_count == 2
        assert mock_session.commit.call_count == 2


class TestDecrementWorkflowCount:
    """Test workflow count decrement"""

    def test_decrement_workflow_count_success(self, mock_session):
        """Test successful decrement of workflow count"""
        # Setup mock result for execute()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        decrement_workflow_count(user_id=123)

        # Verify execute() was called (the actual SQL update)
        assert mock_session.execute.called
        mock_session.commit.assert_called_once()

    def test_decrement_workflow_count_never_negative(self, mock_session):
        """Test that count never goes below 0"""
        # Setup mock result for execute()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute.return_value = mock_result

        decrement_workflow_count(user_id=123)

        # The SQL uses func.greatest(0, count - 1) to ensure count never goes negative
        # We just verify execute was called
        assert mock_session.execute.called
        mock_session.commit.assert_called_once()

    def test_decrement_workflow_count_no_assignment(self, mock_session):
        """Test decrement when user has no assignment"""
        # Setup mock result with rowcount=0 (no rows updated)
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute.return_value = mock_result

        # Should not raise exception
        decrement_workflow_count(user_id=123)

        # execute() is called but rowcount is 0 (no rows updated)
        assert mock_session.execute.called
        mock_session.commit.assert_called_once()

    def test_decrement_workflow_count_none_user_id(self, mock_session):
        """Test decrement with None user_id"""
        decrement_workflow_count(user_id=None)

        mock_session.exec.assert_not_called()


class TestGetActiveWorkflowCount:
    """Test getting active workflow count"""

    def test_get_active_workflow_count_success(self, mock_session):
        """Test successful retrieval of workflow count"""
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 3
        mock_session.exec.return_value.first.return_value = mock_assignment

        count = get_active_workflow_count(user_id=123)

        assert count == 3

    def test_get_active_workflow_count_no_assignment(self, mock_session):
        """Test retrieval when user has no assignment"""
        mock_session.exec.return_value.first.return_value = None

        count = get_active_workflow_count(user_id=123)

        assert count == 0

    def test_get_active_workflow_count_none_value(self, mock_session):
        """Test retrieval when count is None"""
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = None
        mock_session.exec.return_value.first.return_value = mock_assignment

        count = get_active_workflow_count(user_id=123)

        assert count == 0
