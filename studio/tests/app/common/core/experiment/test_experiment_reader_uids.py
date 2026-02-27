"""
Tests for ExptConfigReader.get_local_experiment_uids() (Gap #12).

Tests edge cases including silently swallowed exceptions.
"""

from unittest.mock import MagicMock, patch

from studio.app.common.core.experiment.experiment_reader import ExptConfigReader


class TestGetLocalExperimentUids:
    """Edge case tests for get_local_experiment_uids."""

    def test_returns_empty_set_when_no_configs(self):
        """Returns empty set when no experiment.yaml files found."""
        with patch("glob.glob", return_value=[]):
            result = ExptConfigReader.get_local_experiment_uids("ws1")
        assert result == set()

    def test_extracts_uids_from_paths(self):
        """Extracts UIDs from experiment config paths."""
        paths = [
            "/data/output/ws1/uid_abc/experiment.yaml",
            "/data/output/ws1/uid_def/experiment.yaml",
        ]
        with patch("glob.glob", return_value=paths), patch(
            "studio.app.common.core.experiment.experiment_reader.ExptOutputPathIds"
        ) as mock_ids:
            mock_id1 = MagicMock()
            mock_id1.unique_id = "uid_abc"
            mock_id2 = MagicMock()
            mock_id2.unique_id = "uid_def"
            mock_ids.side_effect = [mock_id1, mock_id2]

            result = ExptConfigReader.get_local_experiment_uids("ws1")

        assert result == {"uid_abc", "uid_def"}

    def test_swallows_exception_for_invalid_path(self):
        """Silently skips paths that raise exceptions during parsing."""
        paths = [
            "/data/output/ws1/good_uid/experiment.yaml",
            "/data/output/ws1/bad_path/experiment.yaml",
        ]
        with patch("glob.glob", return_value=paths), patch(
            "studio.app.common.core.experiment.experiment_reader.ExptOutputPathIds"
        ) as mock_ids:
            mock_good = MagicMock()
            mock_good.unique_id = "good_uid"
            mock_ids.side_effect = [mock_good, ValueError("bad path")]

            result = ExptConfigReader.get_local_experiment_uids("ws1")

        assert result == {"good_uid"}

    def test_skips_entries_with_no_unique_id(self):
        """Skips entries where ExptOutputPathIds returns None unique_id."""
        paths = ["/data/output/ws1/uid1/experiment.yaml"]
        with patch("glob.glob", return_value=paths), patch(
            "studio.app.common.core.experiment.experiment_reader.ExptOutputPathIds"
        ) as mock_ids:
            mock_id = MagicMock()
            mock_id.unique_id = None
            mock_ids.return_value = mock_id

            result = ExptConfigReader.get_local_experiment_uids("ws1")

        assert result == set()
