"""
Unit tests for data cleanup job.

Tests cleanup logic with S3 verification and orphaned data handling.
"""

from unittest.mock import MagicMock, patch

from studio.app.common.core.background.cleanup_job import DataCleanupJob


class TestVerifyS3Backup:
    """Test S3 backup verification"""

    def test_verify_s3_backup_exists(self):
        """Test verification when S3 backup exists"""
        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.head_object.return_value = {}

            with patch.dict("os.environ", {"S3_DEFAULT_BUCKET_NAME": "test-bucket"}):
                result = DataCleanupJob._verify_s3_backup_exists("workspace1", "exp123")

        assert result is True
        assert mock_s3.head_object.call_count == 2  # experiment.yaml, workflow.yaml

    def test_verify_s3_backup_missing(self):
        """Test verification when S3 backup is missing"""
        from botocore.exceptions import ClientError

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            error_response = {"Error": {"Code": "404"}}
            mock_s3.head_object.side_effect = ClientError(error_response, "head_object")

            with patch.dict("os.environ", {"S3_DEFAULT_BUCKET_NAME": "test-bucket"}):
                result = DataCleanupJob._verify_s3_backup_exists("workspace1", "exp123")

        assert result is False

    def test_verify_s3_backup_no_bucket_configured(self):
        """Test verification when S3 bucket not configured"""
        with patch.dict("os.environ", {}, clear=True):
            result = DataCleanupJob._verify_s3_backup_exists("workspace1", "exp123")

        assert result is False


class TestCleanupUserData:
    """Test user data cleanup"""

    def test_cleanup_user_data_with_s3_verification(self):
        """Test cleanup only deletes data with S3 backup"""
        with patch.object(
            DataCleanupJob, "_verify_s3_backup_exists", return_value=True
        ):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.isdir", return_value=True):
                    with patch("os.listdir", return_value=["exp123"]):
                        with patch("shutil.rmtree") as mock_rmtree:
                            result = DataCleanupJob._cleanup_user_data(
                                "123", ["workspace1"]
                            )

        assert result is True
        assert mock_rmtree.call_count >= 1

    def test_cleanup_user_data_keeps_unverified(self):
        """Test cleanup keeps data without S3 backup"""
        with patch.object(
            DataCleanupJob, "_verify_s3_backup_exists", return_value=False
        ):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.isdir", return_value=True):
                    with patch("os.listdir", return_value=["exp123"]):
                        with patch("shutil.rmtree") as mock_rmtree:
                            result = DataCleanupJob._cleanup_user_data(
                                "123", ["workspace1"]
                            )

        assert result is False
        # Input directory is always deleted (1 call), but experiments are kept
        assert mock_rmtree.call_count == 1
        # Verify it was the input directory that was deleted
        assert "input" in str(mock_rmtree.call_args_list[0])


class TestVerifyNoActiveWorkflows:
    """Test workflow verification before cleanup"""

    def test_verify_no_active_workflows_safe(self):
        """Test verification when no workflows active"""
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 0

        with patch(
            "studio.app.common.core.background.cleanup_job.session_scope"
        ) as mock:
            mock_session = MagicMock()
            mock.return_value.__enter__.return_value = mock_session
            mock_session.exec.return_value.first.return_value = mock_assignment

            result = DataCleanupJob._verify_no_active_workflows("123")

        assert result is True

    def test_verify_no_active_workflows_unsafe(self):
        """Test verification when workflows are active"""
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 2

        with patch(
            "studio.app.common.core.background.cleanup_job.session_scope"
        ) as mock:
            mock_session = MagicMock()
            mock.return_value.__enter__.return_value = mock_session
            mock_session.exec.return_value.first.return_value = mock_assignment

            result = DataCleanupJob._verify_no_active_workflows("123")

        assert result is False


class TestHandleOrphanedData:
    """Test orphaned data handling from terminated instances"""

    def test_handle_orphaned_data_terminated_instance(self):
        """Test cleanup of data from terminated instance"""
        mock_assignment = MagicMock()
        mock_assignment.user_id = "123"
        mock_assignment.instance_id = "i-12345"
        mock_assignment.active_workflow_count = 0

        with patch("boto3.client") as mock_boto:
            mock_ec2 = MagicMock()
            mock_boto.return_value = mock_ec2
            mock_ec2.describe_instances.return_value = {
                "Reservations": [{"Instances": [{"State": {"Name": "terminated"}}]}]
            }

            with patch(
                "studio.app.common.core.background.cleanup_job.session_scope"
            ) as mock:
                mock_session = MagicMock()
                mock.return_value.__enter__.return_value = mock_session
                mock_session.exec.return_value.all.return_value = [mock_assignment]
                mock_session.get.return_value = MagicMock(id=123)

                with patch.dict("os.environ", {"INSTANCE_ID": "i-current"}):
                    with patch.object(
                        DataCleanupJob, "_cleanup_user_data", return_value=True
                    ):
                        DataCleanupJob._handle_orphaned_data()

                mock_session.delete.assert_called_once_with(mock_assignment)

    def test_handle_orphaned_data_running_instance(self):
        """Test no cleanup for running instance"""
        mock_assignment = MagicMock()
        mock_assignment.instance_id = "i-12345"

        with patch("boto3.client") as mock_boto:
            mock_ec2 = MagicMock()
            mock_boto.return_value = mock_ec2
            mock_ec2.describe_instances.return_value = {
                "Reservations": [{"Instances": [{"State": {"Name": "running"}}]}]
            }

            with patch(
                "studio.app.common.core.background.cleanup_job.session_scope"
            ) as mock:
                mock_session = MagicMock()
                mock.return_value.__enter__.return_value = mock_session
                mock_session.exec.return_value.all.return_value = [mock_assignment]

                with patch.dict("os.environ", {"INSTANCE_ID": "i-current"}):
                    DataCleanupJob._handle_orphaned_data()

                mock_session.delete.assert_not_called()

    def test_handle_orphaned_data_active_workflows(self):
        """Test no cleanup when workflows are active"""
        mock_assignment = MagicMock()
        mock_assignment.instance_id = "i-12345"
        mock_assignment.active_workflow_count = 1

        with patch("boto3.client") as mock_boto:
            mock_ec2 = MagicMock()
            mock_boto.return_value = mock_ec2
            mock_ec2.describe_instances.return_value = {
                "Reservations": [{"Instances": [{"State": {"Name": "terminated"}}]}]
            }

            with patch(
                "studio.app.common.core.background.cleanup_job.session_scope"
            ) as mock:
                mock_session = MagicMock()
                mock.return_value.__enter__.return_value = mock_session
                mock_session.exec.return_value.all.return_value = [mock_assignment]

                with patch.dict("os.environ", {"INSTANCE_ID": "i-current"}):
                    DataCleanupJob._handle_orphaned_data()

                mock_session.delete.assert_not_called()
