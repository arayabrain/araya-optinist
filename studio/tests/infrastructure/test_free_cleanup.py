"""Tests for free_cleanup Lambda function."""

import json
from unittest.mock import MagicMock, patch

from conftest import MockRow, setup_db_mock


class TestFreeCleanupHandler:
    """Handler-level tests for free_cleanup Lambda."""

    def test_unknown_action(self, mock_env_vars_free):
        """Unknown action returns 400."""
        event = {"action": "nonexistent_action"}
        mock_context = MagicMock()
        mock_context.function_name = "subscr-free-cleanup"

        with patch.dict("os.environ", mock_env_vars_free):
            from free_cleanup import handler

            result = handler(event, mock_context)

            assert result["statusCode"] == 400
            body = json.loads(result["body"])
            assert "unknown action" in body["error"].lower()

    def test_missing_params(self, mock_env_vars_free):
        """simulate_user_activity without user_id returns 400."""
        event = {
            "action": "simulate_user_activity",
            "instance_id": "i-123",
        }
        mock_context = MagicMock()
        mock_context.function_name = "subscr-free-cleanup"

        with patch.dict("os.environ", mock_env_vars_free):
            from free_cleanup import handler

            result = handler(event, mock_context)

            assert result["statusCode"] == 400
            body = json.loads(result["body"])
            assert "missing" in body["error"].lower()


class TestCleanupTestUserSessions:
    """Tests for cleanup_test_user_sessions."""

    def test_empty_emails(self, mock_env_vars_free):
        """Empty email list returns success=False."""
        with patch.dict("os.environ", mock_env_vars_free), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock()
            mock_pymysql.return_value = mock_connection

            from free_cleanup import cleanup_test_user_sessions

            result = cleanup_test_user_sessions([])
            assert result["success"] is False
            assert result["sessions_deleted"] == 0

    def test_no_users_found(self, mock_env_vars_free):
        """Emails don't match any DB users."""
        with patch.dict("os.environ", mock_env_vars_free), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchall_values=[[]],
            )
            mock_pymysql.return_value = mock_connection

            from free_cleanup import cleanup_test_user_sessions

            result = cleanup_test_user_sessions(["nobody@test.com"])
            assert result["success"] is True
            assert result["sessions_deleted"] == 0

    def test_deletes_sessions(self, mock_env_vars_free):
        """Found users, deletes sessions, returns count."""
        with patch.dict("os.environ", mock_env_vars_free), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchall_values=[
                    [
                        MockRow(
                            {
                                "id": 1,
                                "email": "user1@test.com",
                            }
                        ),
                        MockRow(
                            {
                                "id": 2,
                                "email": "user2@test.com",
                            }
                        ),
                    ],
                ],
            )
            mock_pymysql.return_value = mock_connection

            mock_cursor = mock_connection.cursor.return_value.__enter__.return_value
            mock_cursor.rowcount = 3

            from free_cleanup import cleanup_test_user_sessions

            result = cleanup_test_user_sessions(["user1@test.com", "user2@test.com"])
            assert result["success"] is True
            assert result["sessions_deleted"] == 3
            assert result["users_cleaned"] == 2


class TestCleanupAllTestUsers:
    """Tests for cleanup_all_test_users."""

    def test_no_users(self, mock_env_vars_free):
        """No matching users returns 0."""
        with patch.dict("os.environ", mock_env_vars_free), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchall_values=[[]],
            )
            mock_pymysql.return_value = mock_connection

            from free_cleanup import cleanup_all_test_users

            result = cleanup_all_test_users()
            assert result["success"] is True
            assert result["sessions_deleted"] == 0


class TestCountActiveUsers:
    """Tests for count_active_users."""

    def test_returns_correct_count(self, mock_env_vars_free):
        """Returns correct count from DB."""
        with patch.dict("os.environ", mock_env_vars_free), patch(
            "pymysql.connect"
        ) as mock_pymysql:
            mock_connection = setup_db_mock(
                fetchone_values=[
                    MockRow({"count": 15}),
                ],
            )
            mock_pymysql.return_value = mock_connection

            from free_cleanup import count_active_users

            result = count_active_users(activity_threshold_minutes=10)
            assert result["success"] is True
            assert result["active_user_count"] == 15
