"""
Unit tests for common_user_manager Lambda

Run with: python -m pytest test_common_user_manager.py -v
"""

import json
import os
from unittest.mock import MagicMock, Mock, patch

import common_user_manager
import pytest

# Mock environment variables before importing the module
os.environ["RDS_HOST"] = "test-db:3306"
os.environ["RDS_USER"] = "test_user"
os.environ["RDS_PASSWORD"] = "test_password"
os.environ["RDS_DATABASE"] = "test_db"
os.environ["FREE_IDLE_TIMEOUT_HOURS"] = "2"
os.environ["PREMIUM_IDLE_TIMEOUT_HOURS"] = "2"
os.environ["ENV_PREFIX"] = "test"
os.environ[
    "AUTOSCALING_TARGET_GROUP_ARN"
] = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/test/abc123"


@pytest.fixture
def mock_db_connection():
    """Mock database connection (pymysql)"""
    with patch("common_user_manager.get_db_connection") as mock_conn:
        mock_context = MagicMock()
        mock_cursor = MagicMock()
        mock_context.__enter__ = Mock(return_value=mock_context)
        mock_context.__exit__ = Mock(return_value=False)
        mock_context.cursor.return_value.__enter__ = Mock(return_value=mock_cursor)
        mock_context.cursor.return_value.__exit__ = Mock(return_value=False)
        mock_conn.return_value = mock_context
        yield mock_context, mock_cursor


@pytest.fixture
def mock_sqlalchemy_session():
    """Mock SQLAlchemy session"""
    with patch("common_user_manager.get_sqlalchemy_session") as mock_session_ctx:
        mock_session = MagicMock()
        mock_session_ctx.return_value.__enter__ = Mock(return_value=mock_session)
        mock_session_ctx.return_value.__exit__ = Mock(return_value=False)
        yield mock_session


@pytest.fixture
def mock_boto3():
    """Mock boto3 clients"""
    with patch("common_user_manager.boto3") as mock_boto:
        yield mock_boto


class TestRecoverStaleWorkflowCounts:
    """Tests for recover_stale_workflow_counts function"""

    def test_no_stale_workflows(self, mock_sqlalchemy_session):
        """Test when no stale workflows exist"""
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_sqlalchemy_session.execute.return_value = mock_result

        result = common_user_manager.recover_stale_workflow_counts()

        assert result["recovered"] == 0
        assert result["free"] == 0
        assert result["premium"] == 0
        assert "error" not in result
        assert mock_sqlalchemy_session.execute.call_count == 2  # free + premium

    def test_recovers_stale_workflows(self, mock_sqlalchemy_session):
        """Test when stale workflows are found and recovered"""
        mock_result_free = MagicMock()
        mock_result_free.rowcount = 2
        mock_result_premium = MagicMock()
        mock_result_premium.rowcount = 1
        mock_sqlalchemy_session.execute.side_effect = [
            mock_result_free,
            mock_result_premium,
        ]

        result = common_user_manager.recover_stale_workflow_counts()

        assert result["recovered"] == 3
        assert result["free"] == 2
        assert result["premium"] == 1
        assert "error" not in result

    def test_database_error(self, mock_sqlalchemy_session):
        """Test handling of database errors"""
        mock_sqlalchemy_session.execute.side_effect = Exception(
            "Database connection failed"
        )

        result = common_user_manager.recover_stale_workflow_counts()

        assert result["recovered"] == 0
        assert "error" in result
        assert "Database connection failed" in result["error"]


class TestCheckFreeUserInactivity:
    """Tests for check_free_user_inactivity function"""

    def test_no_inactive_users(self, mock_db_connection):
        """Test when no inactive users exist"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = []

        result = common_user_manager.check_free_user_inactivity()

        assert result["logged_out"] == 0
        assert "error" not in result

    def test_logout_inactive_users(self, mock_db_connection):
        """Test logging out inactive users"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = [
            {"user_id": "user1", "instance_id": "i-123"},
            {"user_id": "user2", "instance_id": "i-456"},
        ]

        result = common_user_manager.check_free_user_inactivity()

        assert result["logged_out"] == 2
        assert "error" not in result
        # Verify DELETE was called
        assert mock_cursor.execute.call_count == 2  # SELECT + DELETE
        mock_context.commit.assert_called_once()

    def test_database_error(self, mock_db_connection):
        """Test handling of database errors"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.fetchall.side_effect = Exception("Query failed")

        result = common_user_manager.check_free_user_inactivity()

        assert result["logged_out"] == 0
        assert "error" in result


class TestCheckPremiumUserInactivity:
    """Tests for check_premium_user_inactivity function"""

    def test_no_inactive_users(self, mock_db_connection, mock_boto3):
        """Test when no inactive users exist"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = []

        result = common_user_manager.check_premium_user_inactivity()

        assert result["logged_out"] == 0
        assert "error" not in result

    def test_logout_inactive_premium_users(self, mock_db_connection, mock_boto3):
        """Test logging out inactive premium users with ALB cleanup"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = [
            {
                "user_id": 123,
                "target_group_arn": (
                    "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                    "targetgroup/premium-123-tg/xyz"
                ),
                "alb_rule_arn": (
                    "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                    "listener-rule/app/test/abc/def"
                ),
            }
        ]

        mock_elbv2 = MagicMock()
        mock_boto3.client.return_value = mock_elbv2

        result = common_user_manager.check_premium_user_inactivity()

        assert result["logged_out"] == 1
        assert result.get("failed", 0) == 0
        # Verify ALB cleanup was called
        mock_elbv2.delete_rule.assert_called_once()
        mock_elbv2.delete_target_group.assert_called_once()
        # Verify the per-TG unhealthy-host alarm was cleaned up
        mock_elbv2.delete_alarms.assert_called_once_with(
            AlarmNames=["test-premium-123-tg-unhealthy-hosts"]
        )
        # Verify database deletion
        mock_context.commit.assert_called_once()

    def test_skip_special_target_groups(self, mock_db_connection, mock_boto3):
        """Test that special target groups (standby, autoscaling) are not deleted"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = [
            {
                "user_id": "standby_user",
                "target_group_arn": "standby",
                "alb_rule_arn": "standby",
            }
        ]

        mock_elbv2 = MagicMock()
        mock_boto3.client.return_value = mock_elbv2

        result = common_user_manager.check_premium_user_inactivity()

        assert result["logged_out"] == 1
        # Verify ALB cleanup was NOT called for standby
        mock_elbv2.delete_rule.assert_not_called()
        mock_elbv2.delete_target_group.assert_not_called()
        mock_elbv2.delete_alarms.assert_not_called()

    def test_partial_failure(self, mock_db_connection, mock_boto3):
        """Test when some users fail to logout"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.fetchall.return_value = [
            {
                "user_id": 123,
                "target_group_arn": (
                    "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                    "targetgroup/premium-123-tg/xyz"
                ),
                "alb_rule_arn": (
                    "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                    "listener-rule/app/test/abc/def1"
                ),
            },
            {
                "user_id": 456,
                "target_group_arn": (
                    "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                    "targetgroup/premium-456-tg/xyz"
                ),
                "alb_rule_arn": (
                    "arn:aws:elasticloadbalancing:us-east-1:123456789012:"
                    "listener-rule/app/test/abc/def2"
                ),
            },
        ]

        mock_elbv2 = MagicMock()
        # First user succeeds, second fails
        mock_elbv2.delete_rule.side_effect = [None, Exception("ALB error")]
        mock_boto3.client.return_value = mock_elbv2

        result = common_user_manager.check_premium_user_inactivity()

        assert result["logged_out"] == 1  # Only first user succeeded
        assert result["failed"] == 1  # Second user failed


class TestHandler:
    """Tests for main Lambda handler"""

    def test_successful_execution(self, mock_db_connection, mock_boto3):
        """Test successful handler execution"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.execute.return_value = 0
        mock_cursor.fetchall.return_value = []

        event = {"source": "aws.events"}
        context = MagicMock()
        context.request_id = "test-request-123"

        result = common_user_manager.handler(event, context)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "message" in body
        assert "results" in body

    def test_handler_with_errors(self, mock_db_connection):
        """Test handler gracefully handles operation failures (resilient behavior)"""
        mock_context, mock_cursor = mock_db_connection
        mock_cursor.execute.side_effect = Exception("Critical database error")

        event = {"source": "aws.events"}
        context = MagicMock()
        context.request_id = "test-request-456"

        result = common_user_manager.handler(event, context)

        # Handler should return 200 (resilient) but operations should report errors
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "results" in body
        # Check that operations reported errors in their results
        assert "error" in body["results"]["free_inactivity"]
        assert "error" in body["results"]["premium_inactivity"]
        assert body["results"]["free_inactivity"]["logged_out"] == 0
        assert body["results"]["premium_inactivity"]["logged_out"] == 0


class TestEnvironmentVariables:
    """Tests for environment variable handling"""

    def test_missing_required_env_var(self):
        """Test that missing required env vars raise ValueError"""
        with pytest.raises(ValueError, match="Missing required environment variable"):
            common_user_manager.get_required_env_var("NONEXISTENT_VAR")

    def test_env_var_with_default(self):
        """Test env var with default value"""
        result = common_user_manager.get_required_env_var(
            "NONEXISTENT_VAR", "default_value"
        )
        assert result == "default_value"

    def test_empty_env_var(self):
        """Test that empty env vars are treated as missing"""
        os.environ["EMPTY_VAR"] = ""
        with pytest.raises(ValueError, match="Missing required environment variable"):
            common_user_manager.get_required_env_var("EMPTY_VAR")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
