"""
Unit tests for PublishValidator class.

Tests validation logic for experiment publishing and display eligibility.
"""

from unittest.mock import MagicMock, patch

from studio.app.common.core.dataview.dataview_services import PublishValidator


class TestPublishValidatorValidate:
    """Tests for PublishValidator.validate() method"""

    def test_validate_no_s3_bucket_configured(self):
        """User without S3 bucket cannot publish but can view locally"""
        result = PublishValidator.validate(
            workspace_id="1",
            unique_id="test_exp",
            user_has_s3_bucket=False,
            check_files_on_disk=False,
        )

        assert result.can_publish is False
        assert result.is_displayable is True
        assert "No cloud storage bucket configured" in result.reason

    def test_validate_missing_config_file(self):
        """Missing experiment.yaml prevents publish and display"""
        with patch("os.path.exists", return_value=False):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=False,
            )

        assert result.can_publish is False
        assert result.is_displayable is False
        assert "configuration file is missing" in result.reason

    def test_validate_corrupted_config_file(self):
        """Corrupted experiment.yaml prevents publish and display"""
        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            side_effect=AssertionError("Invalid config"),
        ):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=False,
            )

        assert result.can_publish is False
        assert result.is_displayable is False
        assert "corrupted or invalid" in result.reason

    def test_validate_config_keyerror(self):
        """Config with missing keys prevents publish and display"""
        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            side_effect=KeyError("missing_field"),
        ):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=False,
            )

        assert result.can_publish is False
        assert result.is_displayable is False
        assert "corrupted or invalid" in result.reason

    def test_validate_config_typeerror(self):
        """Config with type errors prevents publish and display"""
        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            side_effect=TypeError("wrong type"),
        ):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=False,
            )

        assert result.can_publish is False
        assert result.is_displayable is False
        assert "corrupted or invalid" in result.reason

    def test_validate_incomplete_config(self):
        """Config missing required fields prevents publish and display"""
        mock_config = MagicMock()

        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            return_value=mock_config,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.validate_experiment_config",
            return_value=False,
        ):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=False,
            )

        assert result.can_publish is False
        assert result.is_displayable is False
        assert "incomplete" in result.reason

    def test_validate_experiment_not_successful(self):
        """Failed experiment cannot be published but can be displayed"""
        mock_config = MagicMock()
        mock_config.success = "error"  # Not SUCCESS

        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            return_value=mock_config,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.validate_experiment_config",
            return_value=True,
        ):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=False,
            )

        assert result.can_publish is False
        assert result.is_displayable is True
        assert "did not complete successfully" in result.reason

    def test_validate_missing_output_directory(self):
        """Missing output directory prevents publish and display"""
        mock_config = MagicMock()
        mock_config.success = "success"

        def path_exists_side_effect(path):
            # Config file exists, output dir does not
            if "experiment.yaml" in str(path) or path.endswith(".yaml"):
                return True
            return False

        with patch("os.path.exists", side_effect=path_exists_side_effect), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            return_value=mock_config,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.validate_experiment_config",
            return_value=True,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.get_config_yaml_path",
            return_value="/path/to/experiment.yaml",
        ):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=True,
            )

        assert result.can_publish is False
        assert result.is_displayable is False
        assert "output directory does not exist" in result.reason

    def test_validate_success(self):
        """All checks pass - experiment can be published and displayed"""
        mock_config = MagicMock()
        mock_config.success = "success"

        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            return_value=mock_config,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.validate_experiment_config",
            return_value=True,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.get_config_yaml_path",
            return_value="/path/to/experiment.yaml",
        ):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=True,
            )

        assert result.can_publish is True
        assert result.is_displayable is True
        assert result.reason is None

    def test_validate_skip_disk_check(self):
        """Skipping disk check allows validation without output directory"""
        mock_config = MagicMock()
        mock_config.success = "success"

        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            return_value=mock_config,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.validate_experiment_config",
            return_value=True,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.get_config_yaml_path",
            return_value="/path/to/experiment.yaml",
        ):
            result = PublishValidator.validate(
                workspace_id="1",
                unique_id="test_exp",
                user_has_s3_bucket=True,
                check_files_on_disk=False,  # Skip disk check
            )

        assert result.can_publish is True
        assert result.is_displayable is True


class TestPublishValidatorValidateForDisplay:
    """Tests for PublishValidator.validate_for_display() method"""

    def test_validate_for_display_success(self):
        """Displayable experiment passes validation"""
        mock_config = MagicMock()
        mock_config.success = "success"

        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            return_value=mock_config,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.validate_experiment_config",
            return_value=True,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.get_config_yaml_path",
            return_value="/path/to/experiment.yaml",
        ):
            result = PublishValidator.validate_for_display(
                workspace_id="1",
                unique_id="test_exp",
            )

        assert result.is_displayable is True

    def test_validate_for_display_ignores_s3_bucket(self):
        """validate_for_display does not check S3 bucket (always passes True)"""
        mock_config = MagicMock()
        mock_config.success = "success"

        with patch("os.path.exists", return_value=True), patch(
            "studio.app.common.core.dataview.dataview_services.ExptConfigReader.read",
            return_value=mock_config,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.validate_experiment_config",
            return_value=True,
        ), patch(
            "studio.app.common.core.dataview.dataview_services."
            "ExptConfigReader.get_config_yaml_path",
            return_value="/path/to/experiment.yaml",
        ):
            # validate_for_display should succeed even if user has no S3 bucket
            # because it passes user_has_s3_bucket=True internally
            result = PublishValidator.validate_for_display(
                workspace_id="1",
                unique_id="test_exp",
            )

        assert result.is_displayable is True
        # can_publish is also True since S3 check is bypassed
        assert result.can_publish is True

    def test_validate_for_display_missing_files(self):
        """Missing files prevent display"""
        with patch("os.path.exists", return_value=False):
            result = PublishValidator.validate_for_display(
                workspace_id="1",
                unique_id="test_exp",
            )

        assert result.is_displayable is False


class TestPublishValidatorCheckOutputFiles:
    """Tests for PublishValidator._check_output_files() internal method"""

    def test_check_output_files_exists(self):
        """Output directory exists returns empty dict"""
        with patch("os.path.exists", return_value=True):
            result = PublishValidator._check_output_files("1", "test_exp")

        assert result == {}

    def test_check_output_files_missing(self):
        """Missing output directory returns error"""
        with patch("os.path.exists", return_value=False):
            result = PublishValidator._check_output_files("1", "test_exp")

        assert "error" in result
        assert "does not exist" in result["error"]
