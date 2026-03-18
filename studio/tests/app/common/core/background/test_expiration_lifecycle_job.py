"""Tests for expiration lifecycle background job."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.background.expiration_lifecycle_job import (
    ExpirationLifecycleJob,
    _DeletionTier,
    _ExperimentInfo,
    _WorkspaceInputInfo,
)

MODULE = "studio.app.common.core.background.expiration_lifecycle_job"


def _mock_session_scope():
    """Create a mock session_scope context manager."""
    mock_scope = MagicMock()
    mock_db = MagicMock()
    mock_scope.return_value.__enter__ = MagicMock(return_value=mock_db)
    mock_scope.return_value.__exit__ = MagicMock(return_value=False)
    return mock_scope, mock_db


def _mock_aioboto3_session():
    """Create a mock aioboto3 session that yields a bucket."""
    mock_bucket = MagicMock()
    mock_s3_resource = AsyncMock()
    mock_s3_resource.Bucket = AsyncMock(return_value=mock_bucket)

    mock_resource_ctx = AsyncMock()
    mock_resource_ctx.__aenter__ = AsyncMock(return_value=mock_s3_resource)
    mock_resource_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.resource.return_value = mock_resource_ctx
    return mock_session, mock_bucket


class TestRun:
    @patch(f"{MODULE}.DIRPATH")
    @pytest.mark.asyncio
    async def test_skips_when_no_bucket(self, mock_dirpath):
        mock_dirpath.DATA_BUCKET_NAME = None
        await ExpirationLifecycleJob.run()

    @patch(f"{MODULE}.DIRPATH")
    @patch(f"{MODULE}.session_scope")
    @patch(
        f"{MODULE}.SubscriptionService.get_users_for_expiration_deletion",
        return_value=[],
    )
    @pytest.mark.asyncio
    async def test_skips_when_no_users(self, mock_get_users, mock_scope, mock_dirpath):
        mock_dirpath.DATA_BUCKET_NAME = "test-bucket"
        mock_scope_obj, _ = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        await ExpirationLifecycleJob.run()
        mock_get_users.assert_called_once()


class TestProcessUser:
    @patch(f"{MODULE}.SubscriptionService.mark_deletion_processed")
    @patch(f"{MODULE}.session_scope")
    @patch(
        f"{MODULE}.SubscriptionService.get_user_subscription",
        return_value=("sub", "plan"),
    )
    @pytest.mark.asyncio
    async def test_skips_resubscribed_user_and_marks_processed(
        self, mock_get_sub, mock_scope, mock_mark
    ):
        mock_scope_obj, _ = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        user_info = {
            "user_id": 1,
            "excess_bytes": 1000,
        }
        await ExpirationLifecycleJob._process_user(user_info, "test-bucket")
        mock_mark.assert_called_once()

    @patch(f"{MODULE}.SubscriptionService.mark_deletion_processed")
    @patch.object(ExpirationLifecycleJob, "_execute_deletion", new_callable=AsyncMock)
    @patch(f"{MODULE}.SubscriptionService.has_active_workflows", return_value=False)
    @patch.object(
        ExpirationLifecycleJob,
        "_fetch_user_data",
        return_value=(
            [
                _ExperimentInfo(
                    workspace_id=1, uid="abc", is_published=False, analyzed_at=None
                )
            ],
            [
                _WorkspaceInputInfo(
                    workspace_id=1, has_published_experiments=False, created_at=None
                )
            ],
        ),
    )
    @patch(
        f"{MODULE}.SubscriptionService.get_deletion_priority",
        return_value="preserve_outputs",
    )
    @patch(
        f"{MODULE}.SubscriptionService.get_current_excess_bytes",
        return_value=5000,
    )
    @patch(f"{MODULE}.session_scope")
    @patch(
        f"{MODULE}.SubscriptionService.get_user_subscription",
        return_value=None,
    )
    @pytest.mark.asyncio
    async def test_executes_deletion_and_marks_processed(
        self,
        mock_get_sub,
        mock_scope,
        mock_excess,
        mock_get_priority,
        mock_fetch,
        mock_workflows,
        mock_execute,
        mock_mark,
    ):
        mock_scope_obj, _ = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        mock_execute.return_value = {
            "succeeded": 1,
            "failed": 0,
            "bytes_deleted": 5000,
            "aborted": False,
        }

        user_info = {
            "user_id": 1,
            "excess_bytes": 5000,
        }
        await ExpirationLifecycleJob._process_user(user_info, "test-bucket")

        mock_execute.assert_called_once()
        mock_mark.assert_called_once()

    @patch(f"{MODULE}.SubscriptionService.mark_deletion_processed")
    @patch(f"{MODULE}.SubscriptionService.has_active_workflows", return_value=False)
    @patch.object(
        ExpirationLifecycleJob,
        "_fetch_user_data",
        return_value=([], []),
    )
    @patch(
        f"{MODULE}.SubscriptionService.get_deletion_priority",
        return_value="preserve_outputs",
    )
    @patch(f"{MODULE}.session_scope")
    @patch(
        f"{MODULE}.SubscriptionService.get_user_subscription",
        return_value=None,
    )
    @pytest.mark.asyncio
    async def test_marks_processed_when_no_deletable_data(
        self,
        mock_get_sub,
        mock_scope,
        mock_get_priority,
        mock_fetch,
        mock_workflows,
        mock_mark,
    ):
        mock_scope_obj, _ = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        user_info = {
            "user_id": 1,
            "excess_bytes": 5000,
        }
        await ExpirationLifecycleJob._process_user(user_info, "test-bucket")
        mock_mark.assert_called_once()

    @patch(
        f"{MODULE}.SubscriptionService.get_deletion_priority",
        return_value="preserve_outputs",
    )
    @patch(f"{MODULE}.session_scope")
    @patch(
        f"{MODULE}.SubscriptionService.get_user_subscription",
        return_value=None,
    )
    @patch(f"{MODULE}.SubscriptionService.has_active_workflows", return_value=True)
    @pytest.mark.asyncio
    async def test_defers_when_active_workflows(
        self, mock_workflows, mock_get_sub, mock_scope, mock_get_priority
    ):
        mock_scope_obj, _ = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        user_info = {
            "user_id": 1,
            "excess_bytes": 5000,
        }
        # Should return without error (deferred, not processed)
        await ExpirationLifecycleJob._process_user(user_info, "test-bucket")

    @patch(f"{MODULE}.SubscriptionService.mark_deletion_processed")
    @patch.object(ExpirationLifecycleJob, "_execute_deletion", new_callable=AsyncMock)
    @patch(f"{MODULE}.SubscriptionService.has_active_workflows", return_value=False)
    @patch.object(
        ExpirationLifecycleJob,
        "_fetch_user_data",
        return_value=(
            [
                _ExperimentInfo(
                    workspace_id=1, uid="abc", is_published=False, analyzed_at=None
                )
            ],
            [],
        ),
    )
    @patch(
        f"{MODULE}.SubscriptionService.get_deletion_priority",
        return_value="preserve_outputs",
    )
    @patch(
        f"{MODULE}.SubscriptionService.get_current_excess_bytes",
        return_value=10000,
    )
    @patch(f"{MODULE}.session_scope")
    @patch(
        f"{MODULE}.SubscriptionService.get_user_subscription",
        return_value=None,
    )
    @pytest.mark.asyncio
    async def test_partial_failure_does_not_mark_processed(
        self,
        mock_get_sub,
        mock_scope,
        mock_excess,
        mock_get_priority,
        mock_fetch,
        mock_workflows,
        mock_execute,
        mock_mark,
    ):
        """Partial failure should NOT mark user as processed."""
        mock_scope_obj, _ = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        mock_execute.return_value = {
            "succeeded": 1,
            "failed": 2,
            "bytes_deleted": 1000,
            "aborted": False,
        }

        user_info = {
            "user_id": 1,
            "excess_bytes": 10000,  # Target not met
        }
        await ExpirationLifecycleJob._process_user(user_info, "test-bucket")
        mock_mark.assert_not_called()

    @patch(f"{MODULE}.SubscriptionService.mark_deletion_processed")
    @patch.object(ExpirationLifecycleJob, "_execute_deletion", new_callable=AsyncMock)
    @patch(f"{MODULE}.SubscriptionService.has_active_workflows", return_value=False)
    @patch.object(
        ExpirationLifecycleJob,
        "_fetch_user_data",
        return_value=(
            [
                _ExperimentInfo(
                    workspace_id=1, uid="abc", is_published=False, analyzed_at=None
                )
            ],
            [],
        ),
    )
    @patch(
        f"{MODULE}.SubscriptionService.get_deletion_priority",
        return_value="preserve_outputs",
    )
    @patch(
        f"{MODULE}.SubscriptionService.get_current_excess_bytes",
        return_value=5000,
    )
    @patch(f"{MODULE}.session_scope")
    @patch(
        f"{MODULE}.SubscriptionService.get_user_subscription",
        return_value=None,
    )
    @pytest.mark.asyncio
    async def test_target_met_despite_failures_marks_processed(
        self,
        mock_get_sub,
        mock_scope,
        mock_excess,
        mock_get_priority,
        mock_fetch,
        mock_workflows,
        mock_execute,
        mock_mark,
    ):
        """Target met despite some failures DOES mark user as processed."""
        mock_scope_obj, _ = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        mock_execute.return_value = {
            "succeeded": 5,
            "failed": 2,
            "bytes_deleted": 10000,
            "aborted": False,
        }

        user_info = {
            "user_id": 1,
            "excess_bytes": 5000,  # Target met (10000 >= 5000)
        }
        await ExpirationLifecycleJob._process_user(user_info, "test-bucket")
        mock_mark.assert_called_once()


class TestExecuteDeletion:
    @pytest.mark.asyncio
    async def test_stops_at_target_bytes(self):
        experiments = [
            _ExperimentInfo(
                workspace_id=1,
                uid="exp1",
                is_published=False,
                analyzed_at=datetime(2025, 1, 1),
            ),
            _ExperimentInfo(
                workspace_id=1,
                uid="exp2",
                is_published=False,
                analyzed_at=datetime(2025, 2, 1),
            ),
        ]

        mock_session, _ = _mock_aioboto3_session()

        with (
            patch.object(
                ExpirationLifecycleJob,
                "_delete_unit",
                new_callable=AsyncMock,
                return_value=5000,
            ) as mock_delete,
            patch(f"{MODULE}.aioboto3.Session", return_value=mock_session),
        ):
            result = await ExpirationLifecycleJob._execute_deletion(
                user_id=1,
                bucket_name="test-bucket",
                experiments=experiments,
                workspace_inputs=[],
                priority="preserve_outputs",
                target_bytes=5000,
                run_id="test_run",
            )

        assert result["succeeded"] == 1
        assert result["bytes_deleted"] == 5000
        assert mock_delete.call_count == 1

    @pytest.mark.asyncio
    async def test_aborts_on_resubscription(self):
        experiments = [
            _ExperimentInfo(
                workspace_id=1,
                uid=f"exp{i}",
                is_published=False,
                analyzed_at=datetime(2025, 1, i + 1),
            )
            for i in range(10)
        ]

        mock_session, _ = _mock_aioboto3_session()

        with (
            patch.object(
                ExpirationLifecycleJob,
                "_delete_unit",
                new_callable=AsyncMock,
                return_value=100,
            ),
            patch.object(
                ExpirationLifecycleJob,
                "_has_active_subscription",
                return_value=True,
            ),
            patch(f"{MODULE}.aioboto3.Session", return_value=mock_session),
        ):
            result = await ExpirationLifecycleJob._execute_deletion(
                user_id=1,
                bucket_name="test-bucket",
                experiments=experiments,
                workspace_inputs=[],
                priority="preserve_outputs",
                target_bytes=999999,
                run_id="test_run",
            )

        assert result["aborted"] is True

    @pytest.mark.asyncio
    async def test_preserve_inputs_order(self):
        """With preserve_inputs, outputs are deleted before inputs."""
        experiments = [
            _ExperimentInfo(
                workspace_id=1,
                uid="exp1",
                is_published=False,
                analyzed_at=datetime(2025, 1, 1),
            ),
        ]
        workspace_inputs = [
            _WorkspaceInputInfo(
                workspace_id=1,
                has_published_experiments=False,
                created_at=datetime(2025, 1, 1),
            ),
        ]

        call_tiers = []

        async def track_delete(user_id, bucket, tier, item, run_id, remaining_bytes=0):
            call_tiers.append(tier)
            return 100

        mock_session, _ = _mock_aioboto3_session()

        with (
            patch.object(
                ExpirationLifecycleJob, "_delete_unit", side_effect=track_delete
            ),
            patch(f"{MODULE}.aioboto3.Session", return_value=mock_session),
        ):
            await ExpirationLifecycleJob._execute_deletion(
                user_id=1,
                bucket_name="test-bucket",
                experiments=experiments,
                workspace_inputs=workspace_inputs,
                priority="preserve_inputs",
                target_bytes=999999,
                run_id="test_run",
            )

        # intermediates first, then outputs, then inputs
        assert call_tiers == [
            _DeletionTier.INTERMEDIATES,
            _DeletionTier.OUTPUTS,
            _DeletionTier.INPUTS,
        ]

    @pytest.mark.asyncio
    async def test_preserve_outputs_order(self):
        """With preserve_outputs, inputs are deleted before outputs."""
        experiments = [
            _ExperimentInfo(
                workspace_id=1,
                uid="exp1",
                is_published=False,
                analyzed_at=datetime(2025, 1, 1),
            ),
        ]
        workspace_inputs = [
            _WorkspaceInputInfo(
                workspace_id=1,
                has_published_experiments=False,
                created_at=datetime(2025, 1, 1),
            ),
        ]

        call_tiers = []

        async def track_delete(user_id, bucket, tier, item, run_id, remaining_bytes=0):
            call_tiers.append(tier)
            return 100

        mock_session, _ = _mock_aioboto3_session()

        with (
            patch.object(
                ExpirationLifecycleJob, "_delete_unit", side_effect=track_delete
            ),
            patch(f"{MODULE}.aioboto3.Session", return_value=mock_session),
        ):
            await ExpirationLifecycleJob._execute_deletion(
                user_id=1,
                bucket_name="test-bucket",
                experiments=experiments,
                workspace_inputs=workspace_inputs,
                priority="preserve_outputs",
                target_bytes=999999,
                run_id="test_run",
            )

        # intermediates first, then inputs, then outputs
        assert call_tiers == [
            _DeletionTier.INTERMEDIATES,
            _DeletionTier.INPUTS,
            _DeletionTier.OUTPUTS,
        ]

    @pytest.mark.asyncio
    async def test_unpublished_deleted_before_published(self):
        """Unpublished experiments should be deleted before published ones."""
        experiments = [
            _ExperimentInfo(
                workspace_id=1,
                uid="published",
                is_published=True,
                analyzed_at=datetime(2025, 1, 1),
            ),
            _ExperimentInfo(
                workspace_id=1,
                uid="unpublished",
                is_published=False,
                analyzed_at=datetime(2025, 6, 1),
            ),
        ]

        deleted_uids = []

        async def track_delete(user_id, bucket, tier, item, run_id, remaining_bytes=0):
            if hasattr(item, "uid"):
                deleted_uids.append(item.uid)
            return 100

        mock_session, _ = _mock_aioboto3_session()

        with (
            patch.object(
                ExpirationLifecycleJob, "_delete_unit", side_effect=track_delete
            ),
            patch(f"{MODULE}.aioboto3.Session", return_value=mock_session),
        ):
            await ExpirationLifecycleJob._execute_deletion(
                user_id=1,
                bucket_name="test-bucket",
                experiments=experiments,
                workspace_inputs=[],
                priority="preserve_outputs",
                target_bytes=999999,
                run_id="test_run",
            )

        assert deleted_uids[0] == "unpublished"

    @pytest.mark.asyncio
    async def test_handles_unit_failure_gracefully(self):
        """Individual unit failures should not abort the entire deletion."""
        experiments = [
            _ExperimentInfo(
                workspace_id=1,
                uid="exp1",
                is_published=False,
                analyzed_at=datetime(2025, 1, 1),
            ),
            _ExperimentInfo(
                workspace_id=1,
                uid="exp2",
                is_published=False,
                analyzed_at=datetime(2025, 2, 1),
            ),
        ]

        call_count = 0

        async def fail_then_succeed(
            user_id, bucket, tier, item, run_id, remaining_bytes=0
        ):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("S3 error")
            return 1000

        mock_session, _ = _mock_aioboto3_session()

        with (
            patch.object(
                ExpirationLifecycleJob, "_delete_unit", side_effect=fail_then_succeed
            ),
            patch.object(ExpirationLifecycleJob, "_update_data_flags"),
            patch(f"{MODULE}.aioboto3.Session", return_value=mock_session),
        ):
            result = await ExpirationLifecycleJob._execute_deletion(
                user_id=1,
                bucket_name="test-bucket",
                experiments=experiments,
                workspace_inputs=[],
                priority="preserve_outputs",
                target_bytes=999999,
                run_id="test_run",
            )

        # First call fails (intermediates/exp1), remaining succeed.
        # Each experiment appears in intermediates + outputs tiers = 4 total calls.
        assert result["failed"] == 1
        assert result["succeeded"] == 3

    @pytest.mark.asyncio
    async def test_calls_update_data_flags(self):
        """_update_data_flags should be called after successful deletion."""
        experiments = [
            _ExperimentInfo(
                workspace_id=1,
                uid="exp1",
                is_published=False,
                analyzed_at=datetime(2025, 1, 1),
            ),
        ]

        mock_session, _ = _mock_aioboto3_session()

        with (
            patch.object(
                ExpirationLifecycleJob,
                "_delete_unit",
                new_callable=AsyncMock,
                return_value=500,
            ),
            patch.object(
                ExpirationLifecycleJob,
                "_update_data_flags",
            ) as mock_flags,
            patch(f"{MODULE}.aioboto3.Session", return_value=mock_session),
        ):
            await ExpirationLifecycleJob._execute_deletion(
                user_id=1,
                bucket_name="test-bucket",
                experiments=experiments,
                workspace_inputs=[],
                priority="preserve_outputs",
                target_bytes=999999,
                run_id="test_run",
            )

        assert mock_flags.call_count > 0


class TestDeleteUnit:
    @pytest.mark.asyncio
    async def test_per_file_failure_continues(self):
        """Per-file S3 failure should skip that file and continue."""
        mock_bucket = MagicMock()
        call_count = 0

        async def mock_delete_objects(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("S3 error on file 2")

        mock_bucket.delete_objects = mock_delete_objects

        keys_with_sizes = [(f"prefix/key_{i}", 100) for i in range(3)]

        with (
            patch.object(
                ExpirationLifecycleJob,
                "_list_experiment_objects",
                new_callable=AsyncMock,
                return_value=keys_with_sizes,
            ),
            patch(f"{MODULE}.decrement_storage_idempotent") as mock_decrement,
            patch(f"{MODULE}.DIRPATH") as mock_dirpath,
        ):
            mock_dirpath.OUTPUT_DIR = "/app/output"
            exp = _ExperimentInfo(
                workspace_id=1, uid="exp1", is_published=False, analyzed_at=None
            )
            result = await ExpirationLifecycleJob._delete_unit(
                user_id=1,
                bucket=mock_bucket,
                tier=_DeletionTier.INTERMEDIATES,
                item=exp,
                run_id="test_run",
                remaining_bytes=999999,
            )

        # File 1 and 3 succeed (200 bytes), file 2 fails
        assert result == 200
        mock_decrement.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_such_bucket_returns_zero(self):
        """NoSuchBucket error should be handled gracefully."""
        mock_bucket = MagicMock()

        class FakeS3Error(Exception):
            def __init__(self):
                self.response = {"Error": {"Code": "NoSuchBucket"}}

        keys_with_sizes = [("prefix/key1", 100)]

        async def mock_delete_objects(**kwargs):
            raise FakeS3Error()

        mock_bucket.delete_objects = mock_delete_objects

        with (
            patch.object(
                ExpirationLifecycleJob,
                "_list_experiment_objects",
                new_callable=AsyncMock,
                return_value=keys_with_sizes,
            ),
            patch(f"{MODULE}.is_no_such_bucket_error", return_value=True),
            patch(f"{MODULE}.decrement_storage_idempotent") as mock_decrement,
            patch(f"{MODULE}.DIRPATH") as mock_dirpath,
        ):
            mock_dirpath.OUTPUT_DIR = "/app/output"
            exp = _ExperimentInfo(
                workspace_id=1, uid="exp1", is_published=False, analyzed_at=None
            )
            result = await ExpirationLifecycleJob._delete_unit(
                user_id=1,
                bucket=mock_bucket,
                tier=_DeletionTier.INTERMEDIATES,
                item=exp,
                run_id="test_run",
                remaining_bytes=999999,
            )

        assert result == 0
        mock_decrement.assert_not_called()


class TestListExperimentObjects:
    @pytest.mark.asyncio
    async def test_classifies_files_correctly(self):
        """YAML files should be protected
        subdirs are intermediates, root files are outputs."""
        mock_objects = []
        prefix = "studio/output/1/exp1/"

        for key, size in [
            (f"{prefix}experiment.yaml", 100),
            (f"{prefix}workflow.yml", 200),
            (f"{prefix}result.npy", 5000),
            (f"{prefix}subdir/temp.dat", 3000),
        ]:
            obj = MagicMock()
            obj.key = key
            obj.size = size
            mock_objects.append(obj)

        async def mock_filter(Prefix=""):
            for obj in mock_objects:
                yield obj

        mock_bucket = MagicMock()
        mock_bucket.objects.filter = mock_filter

        # Intermediates: only subdir/temp.dat
        keys_with_sizes = await ExpirationLifecycleJob._list_experiment_objects(
            mock_bucket, prefix, _DeletionTier.INTERMEDIATES
        )
        assert keys_with_sizes == [(f"{prefix}subdir/temp.dat", 3000)]

        # Outputs: only result.npy
        keys_with_sizes = await ExpirationLifecycleJob._list_experiment_objects(
            mock_bucket, prefix, _DeletionTier.OUTPUTS
        )
        assert keys_with_sizes == [(f"{prefix}result.npy", 5000)]


class TestHasActiveWorkflows:
    def test_returns_true_when_workflows_active(self):
        mock_db = MagicMock()
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 2
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_assignment
        )
        from studio.app.common.core.subscription.subscription_service import (
            SubscriptionService,
        )

        assert SubscriptionService.has_active_workflows(mock_db, 1) is True

    def test_returns_false_when_no_assignment(self):
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        from studio.app.common.core.subscription.subscription_service import (
            SubscriptionService,
        )

        assert SubscriptionService.has_active_workflows(mock_db, 1) is False

    def test_returns_false_when_zero_workflows(self):
        mock_db = MagicMock()
        mock_assignment = MagicMock()
        mock_assignment.active_workflow_count = 0
        mock_db.query.return_value.filter.return_value.first.return_value = (
            mock_assignment
        )
        from studio.app.common.core.subscription.subscription_service import (
            SubscriptionService,
        )

        assert SubscriptionService.has_active_workflows(mock_db, 1) is False


class TestUpdateDataFlags:
    @patch(f"{MODULE}.session_scope")
    def test_clears_intermediates_flag(self, mock_scope):
        """INTERMEDIATES tier should clear has_intermediates on the experiment."""
        mock_scope_obj, mock_db = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        item = _ExperimentInfo(
            workspace_id=1, uid="exp1", is_published=False, analyzed_at=None
        )
        ExpirationLifecycleJob._update_data_flags(
            _DeletionTier.INTERMEDIATES, item, 1000
        )
        mock_db.query.return_value.filter.return_value.update.assert_called_once()

    @patch(f"{MODULE}.session_scope")
    def test_clears_outputs_and_nwb_flags(self, mock_scope):
        """OUTPUTS tier should clear has_outputs and has_nwb."""
        mock_scope_obj, mock_db = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        item = _ExperimentInfo(
            workspace_id=1, uid="exp1", is_published=False, analyzed_at=None
        )
        ExpirationLifecycleJob._update_data_flags(_DeletionTier.OUTPUTS, item, 1000)
        mock_db.query.return_value.filter.return_value.update.assert_called_once()

    @patch(f"{MODULE}.session_scope")
    def test_clears_inputs_flag_workspace_wide(self, mock_scope):
        """INPUTS tier should clear has_inputs on all experiments in workspace."""
        mock_scope_obj, mock_db = _mock_session_scope()
        mock_scope.side_effect = mock_scope_obj.side_effect
        mock_scope.return_value = mock_scope_obj.return_value

        item = _WorkspaceInputInfo(
            workspace_id=1, has_published_experiments=False, created_at=None
        )
        ExpirationLifecycleJob._update_data_flags(_DeletionTier.INPUTS, item, 1000)
        mock_db.query.return_value.filter.return_value.update.assert_called_once()

    def test_skips_when_no_bytes_deleted(self):
        """Should not update flags when bytes_deleted is 0."""
        item = _ExperimentInfo(
            workspace_id=1, uid="exp1", is_published=False, analyzed_at=None
        )
        # Should not raise or open a session
        ExpirationLifecycleJob._update_data_flags(_DeletionTier.INTERMEDIATES, item, 0)
