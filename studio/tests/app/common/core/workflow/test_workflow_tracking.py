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
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 0
        mock_assignment.last_workflow_start = None
        mock_session.exec.return_value.first.return_value = mock_assignment

        increment_workflow_count(user_id=123)

        assert mock_assignment.active_workflow_count == 1
        assert mock_assignment.last_workflow_start is not None
        mock_session.add.assert_called_once_with(mock_assignment)
        mock_session.commit.assert_called_once()

    def test_increment_workflow_count_no_assignment(self, mock_session):
        """Test increment when user has no assignment (premium user)"""
        mock_session.exec.return_value.first.return_value = None

        # Should not raise exception
        increment_workflow_count(user_id=123)

        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

    def test_increment_workflow_count_none_user_id(self, mock_session):
        """Test increment with None user_id"""
        increment_workflow_count(user_id=None)

        mock_session.exec.assert_not_called()

    def test_increment_workflow_count_multiple_times(self, mock_session):
        """Test multiple increments"""
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 0
        mock_session.exec.return_value.first.return_value = mock_assignment

        increment_workflow_count(user_id=123)
        assert mock_assignment.active_workflow_count == 1

        increment_workflow_count(user_id=123)
        assert mock_assignment.active_workflow_count == 2


class TestDecrementWorkflowCount:
    """Test workflow count decrement"""

    def test_decrement_workflow_count_success(self, mock_session):
        """Test successful decrement of workflow count"""
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 2
        mock_assignment.last_workflow_end = None
        mock_session.exec.return_value.first.return_value = mock_assignment

        decrement_workflow_count(user_id=123)

        assert mock_assignment.active_workflow_count == 1
        assert mock_assignment.last_workflow_end is not None
        mock_session.add.assert_called_once_with(mock_assignment)
        mock_session.commit.assert_called_once()

    def test_decrement_workflow_count_never_negative(self, mock_session):
        """Test that count never goes below 0"""
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 0
        mock_session.exec.return_value.first.return_value = mock_assignment

        decrement_workflow_count(user_id=123)

        assert mock_assignment.active_workflow_count == 0

    def test_decrement_workflow_count_no_assignment(self, mock_session):
        """Test decrement when user has no assignment"""
        mock_session.exec.return_value.first.return_value = None

        # Should not raise exception
        decrement_workflow_count(user_id=123)

        mock_session.add.assert_not_called()
        mock_session.commit.assert_not_called()

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
