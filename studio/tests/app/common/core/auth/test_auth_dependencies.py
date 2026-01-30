"""
Unit tests for auth dependencies, specifically for public outputs bucket resolution.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from studio.app.common.core.auth.auth_dependencies import (
    _get_user_remote_bucket_name,
    get_current_user_for_dataview_outputs,
    get_outputs_remote_bucket_name,
)


class TestGetCurrentUserForDataviewOutputs:
    """Test get_current_user_for_dataview_outputs dependency"""

    @pytest.mark.asyncio
    async def test_returns_none_for_public_request_without_credentials(self):
        """Public requests without credentials should return None"""
        mock_req = MagicMock()
        mock_res = MagicMock()
        mock_db = MagicMock()

        with patch(
            "studio.app.common.core.auth.auth_dependencies.DataviewService."
            "is_dataview_public_outputs_request",
            return_value=True,
        ):
            result = await get_current_user_for_dataview_outputs(
                req=mock_req,
                res=mock_res,
                ex_token=None,
                credential=None,
                db=mock_db,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_user_for_authenticated_public_request(self):
        """Public requests with valid credentials should return user"""
        mock_req = MagicMock()
        mock_res = MagicMock()
        mock_db = MagicMock()
        mock_credential = MagicMock()
        mock_user = MagicMock()

        with patch(
            "studio.app.common.core.auth.auth_dependencies.DataviewService."
            "is_dataview_public_outputs_request",
            return_value=True,
        ):
            with patch(
                "studio.app.common.core.auth.auth_dependencies.get_current_user",
                new_callable=AsyncMock,
                return_value=mock_user,
            ):
                result = await get_current_user_for_dataview_outputs(
                    req=mock_req,
                    res=mock_res,
                    ex_token=None,
                    credential=mock_credential,
                    db=mock_db,
                )

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_returns_none_for_invalid_credentials_on_public_request(self):
        """Public requests with invalid credentials should return None (not raise)"""
        from fastapi import HTTPException

        mock_req = MagicMock()
        mock_res = MagicMock()
        mock_db = MagicMock()
        mock_credential = MagicMock()

        with patch(
            "studio.app.common.core.auth.auth_dependencies.DataviewService."
            "is_dataview_public_outputs_request",
            return_value=True,
        ):
            with patch(
                "studio.app.common.core.auth.auth_dependencies.get_current_user",
                new_callable=AsyncMock,
                side_effect=HTTPException(status_code=401, detail="Invalid token"),
            ):
                result = await get_current_user_for_dataview_outputs(
                    req=mock_req,
                    res=mock_res,
                    ex_token=None,
                    credential=mock_credential,
                    db=mock_db,
                )

        # Should return None, not raise
        assert result is None

    @pytest.mark.asyncio
    async def test_requires_auth_for_non_public_request(self):
        """Non-public requests should require authentication"""
        mock_req = MagicMock()
        mock_res = MagicMock()
        mock_db = MagicMock()
        mock_user = MagicMock()

        with patch(
            "studio.app.common.core.auth.auth_dependencies.DataviewService."
            "is_dataview_public_outputs_request",
            return_value=False,
        ):
            with patch(
                "studio.app.common.core.auth.auth_dependencies.get_current_user",
                new_callable=AsyncMock,
                return_value=mock_user,
            ):
                result = await get_current_user_for_dataview_outputs(
                    req=mock_req,
                    res=mock_res,
                    ex_token="valid_token",
                    credential=None,
                    db=mock_db,
                )

        assert result == mock_user


class TestGetOutputsRemoteBucketName:
    """Test get_outputs_remote_bucket_name dependency"""

    @pytest.mark.asyncio
    async def test_returns_user_bucket_when_no_workspace_id(self):
        """Authenticated user should get their own bucket
        when workspace can't be determined"""
        mock_req = MagicMock()
        mock_req.url.path = "/outputs/image/some/path/without/workspace"
        mock_req.query_params = {}
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.remote_bucket_name = "user-bucket-123"

        with patch("studio.app.dir_path.DIRPATH") as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"
            result = await get_outputs_remote_bucket_name(
                req=mock_req,
                current_user=mock_user,
                db=mock_db,
            )

        assert result == "user-bucket-123"

    @pytest.mark.asyncio
    async def test_returns_workspace_owner_bucket_for_authenticated_user(self):
        """Authenticated user with workspace access should
        get workspace owner's bucket"""
        mock_req = MagicMock()
        mock_req.url.path = (
            "/outputs/image//app/studio_data/output/123/abc123/file.json"
        )
        mock_req.query_params = {}
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.remote_bucket_name = "user-bucket-123"

        mock_workspace = MagicMock()
        mock_workspace.user = MagicMock()
        mock_workspace.user.remote_bucket_name = "owner-bucket-456"

        with patch("studio.app.dir_path.DIRPATH") as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"
            with patch(
                "studio.app.common.core.experiment.experiment.ExptOutputPathIds"
            ) as mock_path_ids_class:
                mock_path_ids = MagicMock()
                mock_path_ids.workspace_id = "123"
                mock_path_ids.unique_id = "abc123"
                mock_path_ids_class.return_value = mock_path_ids

                # Mock the join query chain for authenticated users
                mock_query = mock_db.query.return_value
                mock_query.join.return_value.filter.return_value.first.return_value = (
                    mock_workspace
                )

                result = await get_outputs_remote_bucket_name(
                    req=mock_req,
                    current_user=mock_user,
                    db=mock_db,
                )

        # Should use workspace owner's bucket, not the current user's bucket
        assert result == "owner-bucket-456"

    @pytest.mark.asyncio
    async def test_returns_user_bucket_when_no_workspace_access(self):
        """Authenticated user without workspace access should get their own bucket"""
        mock_req = MagicMock()
        mock_req.url.path = (
            "/outputs/image//app/studio_data/output/999/abc123/file.json"
        )
        mock_req.query_params = {}
        mock_db = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.remote_bucket_name = "user-bucket-123"

        with patch("studio.app.dir_path.DIRPATH") as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"
            with patch(
                "studio.app.common.core.experiment.experiment.ExptOutputPathIds"
            ) as mock_path_ids_class:
                mock_path_ids = MagicMock()
                mock_path_ids.workspace_id = "999"
                mock_path_ids.unique_id = "abc123"
                mock_path_ids_class.return_value = mock_path_ids

                # Mock the join query chain - user has no access, returns None
                mock_query = mock_db.query.return_value
                mock_query.join.return_value.filter.return_value.first.return_value = (
                    None
                )

                result = await get_outputs_remote_bucket_name(
                    req=mock_req,
                    current_user=mock_user,
                    db=mock_db,
                )

        # Should fall back to user's own bucket since they don't have workspace access
        assert result == "user-bucket-123"

    @pytest.mark.asyncio
    async def test_returns_workspace_owner_bucket_for_public_request(self):
        """Public request should get workspace owner's bucket"""
        mock_req = MagicMock()
        mock_req.url.path = (
            "/outputs/image//app/studio_data/output/123/abc123/file.json"
        )
        mock_req.query_params = {}
        mock_db = MagicMock()

        mock_workspace = MagicMock()
        mock_workspace.user = MagicMock()
        mock_workspace.user.remote_bucket_name = "owner-bucket-456"

        with patch("studio.app.dir_path.DIRPATH") as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"
            with patch(
                "studio.app.common.core.experiment.experiment.ExptOutputPathIds"
            ) as mock_path_ids_class:
                mock_path_ids = MagicMock()
                mock_path_ids.workspace_id = "123"
                mock_path_ids.unique_id = "abc123"
                mock_path_ids_class.return_value = mock_path_ids

                mock_query = mock_db.query.return_value
                mock_query.filter.return_value.first.return_value = mock_workspace

                result = await get_outputs_remote_bucket_name(
                    req=mock_req,
                    current_user=None,
                    db=mock_db,
                )

        assert result == "owner-bucket-456"

    @pytest.mark.asyncio
    async def test_returns_default_bucket_for_public_request_without_workspace(self):
        """Public request without workspace should fall back to default bucket"""
        import os

        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageType,
        )

        mock_req = MagicMock()
        mock_req.url.path = "/outputs/image/some/invalid/path"
        mock_req.query_params = {}
        mock_db = MagicMock()

        with patch("studio.app.dir_path.DIRPATH") as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"
            with patch(
                "studio.app.common.core.experiment.experiment.ExptOutputPathIds",
                side_effect=ValueError("Invalid path"),
            ):
                with patch.dict(
                    os.environ, {"S3_DEFAULT_BUCKET_NAME": "default-bucket"}
                ):
                    with patch(
                        "studio.app.common.core.auth.auth_dependencies."
                        "RemoteStorageType.get_activated_type",
                        return_value=RemoteStorageType.S3,
                    ):
                        result = await get_outputs_remote_bucket_name(
                            req=mock_req,
                            current_user=None,
                            db=mock_db,
                        )

        assert result == "default-bucket"

    @pytest.mark.asyncio
    async def test_extracts_workspace_id_from_query_params(self):
        """Should extract workspace_id from query params as fallback"""
        mock_req = MagicMock()
        mock_req.url.path = "/outputs/image/some/path"
        mock_req.query_params = {"workspace_id": "456"}
        mock_db = MagicMock()

        mock_workspace = MagicMock()
        mock_workspace.user = MagicMock()
        mock_workspace.user.remote_bucket_name = "owner-bucket-from-query"

        with patch("studio.app.dir_path.DIRPATH") as mock_dirpath:
            mock_dirpath.OUTPUT_DIR = "/app/studio_data/output"
            with patch(
                "studio.app.common.core.experiment.experiment.ExptOutputPathIds",
                side_effect=ValueError("Invalid path"),
            ):
                mock_query = mock_db.query.return_value
                mock_query.filter.return_value.first.return_value = mock_workspace

                result = await get_outputs_remote_bucket_name(
                    req=mock_req,
                    current_user=None,
                    db=mock_db,
                )

        assert result == "owner-bucket-from-query"


class TestGetUserRemoteBucketName:
    """Test _get_user_remote_bucket_name helper"""

    def test_returns_user_bucket_when_available(self):
        """Should return user's bucket name when user has one"""
        mock_user = MagicMock()
        mock_user.remote_bucket_name = "my-bucket"

        result = _get_user_remote_bucket_name(mock_user)

        assert result == "my-bucket"

    def test_returns_default_bucket_when_user_has_no_bucket(self):
        """Should return default bucket when user has no custom bucket"""
        import os

        mock_user = MagicMock()
        mock_user.remote_bucket_name = None

        with patch.dict(os.environ, {"S3_DEFAULT_BUCKET_NAME": "default-bucket"}):
            with patch(
                "studio.app.common.core.auth.auth_dependencies."
                "RemoteStorageType.get_activated_type"
            ) as mock_storage_type:
                from studio.app.common.core.storage.remote_storage_controller import (
                    RemoteStorageType,
                )

                mock_storage_type.return_value = RemoteStorageType.S3

                result = _get_user_remote_bucket_name(mock_user)

        assert result == "default-bucket"

    def test_returns_default_bucket_when_no_user(self):
        """Should return default bucket when no user provided"""
        import os

        with patch.dict(os.environ, {"S3_DEFAULT_BUCKET_NAME": "default-bucket"}):
            with patch(
                "studio.app.common.core.auth.auth_dependencies."
                "RemoteStorageType.get_activated_type"
            ) as mock_storage_type:
                from studio.app.common.core.storage.remote_storage_controller import (
                    RemoteStorageType,
                )

                mock_storage_type.return_value = RemoteStorageType.S3

                result = _get_user_remote_bucket_name(None)

        assert result == "default-bucket"
