"""
Unit tests for data cleanup job.

Tests cleanup logic with S3 verification and orphaned data handling.
"""

from unittest.mock import MagicMock, patch

import pytest

from studio.app.common.core.background.cleanup_job import DataCleanupJob


@pytest.fixture(autouse=True)
def _reset_instance_id_cache():
    """Clear the process-lifetime instance-id cache between tests."""
    DataCleanupJob._instance_id_cache = None
    yield
    DataCleanupJob._instance_id_cache = None


class TestVerifyS3Backup:
    """Test S3 backup verification"""

    def test_verify_s3_backup_exists(self):
        """Test verification when S3 backup exists"""
        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            mock_s3.head_object.return_value = {}

            result = DataCleanupJob._verify_s3_backup_exists(
                "test-bucket", "workspace1", "exp123"
            )

        assert result is True
        assert mock_s3.head_object.call_count == 2  # experiment.yaml, workflow.yaml
        # head_object must target the passed (per-user) bucket, not a default one
        for call in mock_s3.head_object.call_args_list:
            assert call.kwargs["Bucket"] == "test-bucket"

    def test_verify_s3_backup_missing(self):
        """Test verification when S3 backup is missing"""
        from botocore.exceptions import ClientError

        with patch("boto3.client") as mock_boto:
            mock_s3 = MagicMock()
            mock_boto.return_value = mock_s3
            error_response = {"Error": {"Code": "404"}}
            mock_s3.head_object.side_effect = ClientError(error_response, "head_object")

            result = DataCleanupJob._verify_s3_backup_exists(
                "test-bucket", "workspace1", "exp123"
            )

        assert result is False

    def test_verify_s3_backup_no_bucket_resolved(self):
        """No bucket resolved → keep data (return False), never call S3"""
        with patch("boto3.client") as mock_boto:
            result = DataCleanupJob._verify_s3_backup_exists(
                None, "workspace1", "exp123"
            )

        assert result is False
        mock_boto.assert_not_called()


class TestResolveUserBucketName:
    """Test per-user S3 bucket resolution used before deletion"""

    def test_prefers_remote_bucket_name_from_db(self):
        """The bucket recorded on the user row wins (authoritative)."""
        user = MagicMock()
        user.remote_bucket_name = "development-optinist-user-7-abc123"

        mock_db = MagicMock()
        mock_db.execute.return_value.first.return_value = (user,)

        cm = MagicMock()
        cm.__enter__.return_value = mock_db
        cm.__exit__.return_value = False

        with patch(
            "studio.app.common.core.background.cleanup_job.session_scope",
            return_value=cm,
        ):
            result = DataCleanupJob._resolve_user_bucket_name("7")

        assert result == "development-optinist-user-7-abc123"

    def test_falls_back_to_deterministic_name(self):
        """No DB bucket → derive it with the writer's formula/prefix."""
        mock_db = MagicMock()
        mock_db.execute.return_value.first.return_value = None  # no user row

        cm = MagicMock()
        cm.__enter__.return_value = mock_db
        cm.__exit__.return_value = False

        with patch(
            "studio.app.common.core.background.cleanup_job.session_scope",
            return_value=cm,
        ):
            with patch.dict(
                "os.environ", {"S3_USER_BUCKET_PREFIX": "development-optinist-user"}
            ):
                with patch(
                    "studio.app.common.core.storage.remote_storage_controller."
                    "RemoteStorageController.create_user_bucket_name",
                    return_value="development-optinist-user-7-abc123",
                ) as mock_create:
                    result = DataCleanupJob._resolve_user_bucket_name("7")

        mock_create.assert_called_once_with(
            id=7, prefix="development-optinist-user"
        )
        assert result == "development-optinist-user-7-abc123"


class TestCleanupUserData:
    """Test user data cleanup"""

    @patch.object(
        DataCleanupJob, "_resolve_user_bucket_name", return_value="user-bucket"
    )
    @patch.object(DataCleanupJob, "_check_user_relogin", return_value=False)
    def test_cleanup_user_data_with_s3_verification(self, mock_relogin, mock_bucket):
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

    @patch.object(
        DataCleanupJob, "_resolve_user_bucket_name", return_value="user-bucket"
    )
    @patch.object(DataCleanupJob, "_check_user_relogin", return_value=False)
    def test_cleanup_user_data_keeps_unverified(self, mock_relogin, mock_bucket):
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
            # Mock execute() to return a row-like tuple
            mock_session.execute.return_value.first.return_value = (mock_assignment,)

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
            # Mock execute() to return a row-like tuple
            mock_session.execute.return_value.first.return_value = (mock_assignment,)

            result = DataCleanupJob._verify_no_active_workflows("123")

        assert result is False


class TestHandleOrphanedData:
    """Test orphaned data handling from terminated instances"""

    def test_handle_orphaned_data_terminated_instance(self):
        """Test DB-only cleanup of orphaned assignment from terminated instance.

        When an instance is terminated its EBS is destroyed, so
        _cleanup_orphaned_assignment should only remove the DB record
        (no _cleanup_user_data call).
        """
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
                mock_session.execute.return_value.all.return_value = [
                    (mock_assignment,)
                ]

                with patch.dict("os.environ", {"INSTANCE_ID": "i-current"}):
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
                # Mock execute() to return row-like tuples
                mock_session.execute.return_value.all.return_value = [
                    (mock_assignment,)
                ]

                with patch.dict("os.environ", {"INSTANCE_ID": "i-current"}):
                    DataCleanupJob._handle_orphaned_data()

                mock_session.delete.assert_not_called()

    def test_handle_orphaned_data_empty_reservations(self):
        """Test DB-only cleanup when EC2 returns empty Reservations"""
        mock_assignment = MagicMock()
        mock_assignment.user_id = "123"
        mock_assignment.instance_id = "i-12345"
        mock_assignment.active_workflow_count = 0

        with patch("boto3.client") as mock_boto:
            mock_ec2 = MagicMock()
            mock_boto.return_value = mock_ec2
            mock_ec2.describe_instances.return_value = {"Reservations": []}

            with patch(
                "studio.app.common.core.background.cleanup_job.session_scope"
            ) as mock:
                mock_session = MagicMock()
                mock.return_value.__enter__.return_value = mock_session
                mock_session.execute.return_value.all.return_value = [
                    (mock_assignment,)
                ]

                with patch.dict("os.environ", {"INSTANCE_ID": "i-current"}):
                    DataCleanupJob._handle_orphaned_data()

                mock_session.delete.assert_called_once_with(mock_assignment)

    def test_handle_orphaned_data_skipped_on_free_tier(self):
        """Test _handle_orphaned_data is skipped when ENABLE_LOCAL_CLEANUP=1"""
        import asyncio

        with patch("boto3.client"):
            with patch.dict(
                "os.environ",
                {"INSTANCE_ID": "i-current", "ENABLE_LOCAL_CLEANUP": "1"},
            ):
                # run() should NOT call _handle_orphaned_data
                with patch.object(
                    DataCleanupJob, "_handle_orphaned_data"
                ) as mock_orphan:
                    with patch.object(
                        DataCleanupJob, "_get_users_for_cleanup", return_value=[]
                    ):
                        asyncio.run(DataCleanupJob.run())
                    mock_orphan.assert_not_called()

    def test_handle_orphaned_data_runs_on_background_service(self):
        """Test _handle_orphaned_data runs when ENABLE_LOCAL_CLEANUP is not set"""
        import asyncio

        with patch.dict("os.environ", {"INSTANCE_ID": "i-bg"}, clear=False):
            # Ensure ENABLE_LOCAL_CLEANUP is not set
            with patch.dict("os.environ", {}, clear=False):
                import os as _os

                _os.environ.pop("ENABLE_LOCAL_CLEANUP", None)

                with patch.object(
                    DataCleanupJob, "_handle_orphaned_data"
                ) as mock_orphan:
                    with patch.object(
                        DataCleanupJob, "_get_users_for_cleanup", return_value=[]
                    ):
                        asyncio.run(DataCleanupJob.run())
                    mock_orphan.assert_called_once()


class TestGetCurrentInstanceId:
    """Test _get_current_instance_id helper"""

    def test_returns_env_var_when_set(self):
        with patch.dict("os.environ", {"INSTANCE_ID": "i-abc12345"}):
            assert DataCleanupJob._get_current_instance_id() == "i-abc12345"

    def test_returns_local_when_unset_and_no_metadata(self):
        # env unset → IMDS attempted → both fail → "local"
        with patch.dict("os.environ", {}, clear=True):
            with patch("urllib.request.urlopen", side_effect=Exception("no imds")):
                assert DataCleanupJob._get_current_instance_id() == "local"

    def test_returns_local_for_empty_string_and_no_metadata(self):
        with patch.dict("os.environ", {"INSTANCE_ID": ""}):
            with patch("urllib.request.urlopen", side_effect=Exception("no imds")):
                assert DataCleanupJob._get_current_instance_id() == "local"

    def test_falls_back_to_imds_when_env_unset(self):
        """When env is unset, the worker resolves via IMDS (matches middleware),
        so the per-instance filter is not silently dropped."""
        from unittest.mock import MagicMock

        token_resp = MagicMock()
        token_resp.read.return_value = b"token123"
        token_resp.__enter__.return_value = token_resp
        id_resp = MagicMock()
        id_resp.read.return_value = b"i-fromimds"
        id_resp.__enter__.return_value = id_resp

        with patch.dict("os.environ", {}, clear=True):
            with patch("urllib.request.urlopen", side_effect=[token_resp, id_resp]):
                assert DataCleanupJob._get_current_instance_id() == "i-fromimds"


class TestGetUsersForCleanupInstanceFilter:
    """Test instance_id filtering in _get_users_for_cleanup"""

    def test_filters_by_instance_id_when_set(self):
        """When INSTANCE_ID is set, query should include instance_id filter"""
        with patch.dict("os.environ", {"INSTANCE_ID": "i-abc12345"}):
            with patch(
                "studio.app.common.core.background.cleanup_job.session_scope"
            ) as mock_scope:
                mock_session = MagicMock()
                mock_scope.return_value.__enter__.return_value = mock_session
                mock_session.execute.return_value = iter([])

                result = DataCleanupJob._get_users_for_cleanup()

                assert result == []
                # Verify the SQL was executed (with instance_id filter)
                mock_session.execute.assert_called_once()
                # Check the compiled SQL contains instance_id
                call_args = mock_session.execute.call_args
                stmt = call_args[0][0]
                compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
                assert "instance_id" in compiled

    @patch.object(DataCleanupJob, "_get_current_instance_id", return_value="local")
    def test_no_filter_in_dev_mode(self, mock_instance):
        """When resolver returns 'local' (dev / no metadata), no instance filter"""
        with patch.dict("os.environ", {}, clear=True):
            with patch(
                "studio.app.common.core.background.cleanup_job.session_scope"
            ) as mock_scope:
                mock_session = MagicMock()
                mock_scope.return_value.__enter__.return_value = mock_session
                mock_session.execute.return_value = iter([])

                result = DataCleanupJob._get_users_for_cleanup()

                assert result == []
                mock_session.execute.assert_called_once()
                call_args = mock_session.execute.call_args
                stmt = call_args[0][0]
                compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
                assert "instance_id" not in compiled


class TestCleanupUserDataNotFound:
    """Test _cleanup_user_data no-data behavior (finding #1)"""

    @patch.object(DataCleanupJob, "_get_current_instance_id", return_value="local")
    @patch.object(DataCleanupJob, "_check_user_relogin", return_value=False)
    def test_returns_false_when_no_data_unfiltered(self, mock_relogin, mock_instance):
        """Unfiltered run (local / background service): no data → False,
        because the user's data may live on another instance."""
        with patch("os.path.exists", return_value=False):
            result = DataCleanupJob._cleanup_user_data("123", ["workspace1"])

        assert result is False

    @patch.object(DataCleanupJob, "_get_current_instance_id", return_value="i-abc12345")
    @patch.object(DataCleanupJob, "_check_user_relogin", return_value=False)
    def test_returns_true_when_no_data_on_owning_instance(
        self, mock_relogin, mock_instance
    ):
        """Instance-filtered run: the filter guarantees ownership, so an
        empty user is genuinely clean → True, letting _mark_cleaned close
        the assignment / usage log (prevents the leak in finding #1)."""
        with patch("os.path.exists", return_value=False):
            result = DataCleanupJob._cleanup_user_data("123", ["workspace1"])

        assert result is True

    @patch.object(
        DataCleanupJob, "_resolve_user_bucket_name", return_value="user-bucket"
    )
    @patch.object(DataCleanupJob, "_check_user_relogin", return_value=False)
    def test_returns_true_when_data_exists_and_cleaned(
        self, mock_relogin, mock_bucket
    ):
        """When data exists and is fully cleaned, returns True"""
        with patch.object(
            DataCleanupJob, "_verify_s3_backup_exists", return_value=True
        ):
            with patch("os.path.exists", return_value=True):
                with patch("os.path.isdir", return_value=True):
                    with patch("os.listdir", return_value=["exp123"]):
                        with patch("shutil.rmtree"):
                            result = DataCleanupJob._cleanup_user_data(
                                "123", ["workspace1"]
                            )

        assert result is True


class TestCleanupOrphanedAssignmentNoFileCleanup:
    """Test _cleanup_orphaned_assignment does NOT call _cleanup_user_data"""

    def test_does_not_call_cleanup_user_data(self):
        """Terminated instance EBS is destroyed — only DB record removal needed"""
        mock_assignment = MagicMock()
        mock_assignment.user_id = "123"
        mock_assignment.instance_id = "i-12345"

        mock_db = MagicMock()

        with patch.object(DataCleanupJob, "_cleanup_user_data") as mock_cleanup:
            result = DataCleanupJob._cleanup_orphaned_assignment(
                mock_db, mock_assignment
            )

        assert result is True
        mock_cleanup.assert_not_called()
        mock_db.delete.assert_called_once_with(mock_assignment)
        mock_db.commit.assert_called_once()
