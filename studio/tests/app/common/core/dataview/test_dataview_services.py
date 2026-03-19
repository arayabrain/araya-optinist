"""Tests for DataviewService.select_best_thumbnail_input."""

from unittest.mock import MagicMock

import pytest

from studio.app.common.core.workflow.workflow import (
    Node,
    NodeData,
    NodePosition,
    NodeType,
    Style,
)
from studio.app.common.schemas.workflow import WorkflowConfig


def _make_node(node_id, node_type, path, hdf5_path=None, mat_path=None):
    """Helper to create a Node with minimal required fields."""
    return Node(
        id=node_id,
        type=node_type,
        data=NodeData(
            label=node_id,
            param={},
            path=path,
            type="input",
            hdf5Path=hdf5_path,
            matPath=mat_path,
        ),
        position=NodePosition(x=0, y=0),
        style=Style(),
    )


class TestSelectBestThumbnailInput:
    """Tests for select_best_thumbnail_input path handling."""

    def _call(self, nodes):
        from studio.app.common.core.dataview.dataview_services import DataviewService

        wf = WorkflowConfig(
            nodeDict={n.id: n for n in nodes},
            edgeDict={},
        )
        return DataviewService.select_best_thumbnail_input(wf)

    def test_hdf5_string_path_returns_full_filename(self):
        """HDF5 nodes store path as a string; must not index into characters."""
        node = _make_node(
            "input_1", NodeType.HDF5, "sample_hdf5.h5", hdf5_path="data/image"
        )
        image_url, ds = self._call([node])
        assert image_url == "sample_hdf5.h5"
        assert ds.hdf5_path == "data/image"
        assert ds.mat_path is None

    def test_mat_string_path_returns_full_filename(self):
        """MAT nodes store path as a string; must not index into characters."""
        node = _make_node(
            "input_1", NodeType.MATLAB, "sample.mat", mat_path="data/behavior"
        )
        image_url, ds = self._call([node])
        assert image_url == "sample.mat"
        assert ds.mat_path == "data/behavior"

    def test_image_list_path_returns_first_element(self):
        """Image nodes store path as a list; [0] should return the filename."""
        node = _make_node("input_1", NodeType.IMAGE, ["my_image.tiff"])
        image_url, ds = self._call([node])
        assert image_url == "my_image.tiff"

    def test_hdf5_wins_over_mat_in_multi_input(self):
        """HDF5 (priority 2) should be selected over MAT (priority 1)."""
        mat_node = _make_node(
            "mat_in", NodeType.MATLAB, "behavior.mat", mat_path="data/b"
        )
        hdf5_node = _make_node(
            "hdf5_in", NodeType.HDF5, "imaging.h5", hdf5_path="data/image"
        )
        image_url, ds = self._call([mat_node, hdf5_node])
        assert image_url == "imaging.h5"
        assert ds.hdf5_path == "data/image"

    def test_image_wins_over_hdf5(self):
        """IMAGE (priority 3) should be selected over HDF5 (priority 2)."""
        hdf5_node = _make_node(
            "hdf5_in", NodeType.HDF5, "imaging.h5", hdf5_path="data/image"
        )
        img_node = _make_node("img_in", NodeType.IMAGE, ["photo.tiff"])
        image_url, ds = self._call([hdf5_node, img_node])
        assert image_url == "photo.tiff"

    def test_no_input_nodes_returns_none(self):
        """Algorithm-only workflows return (None, empty DatasetPaths)."""
        algo_node = _make_node("algo_1", NodeType.ALGO, "some/algo")
        image_url, ds = self._call([algo_node])
        assert image_url is None

    def test_empty_workflow(self):
        """Empty workflow returns (None, empty DatasetPaths)."""
        image_url, ds = self._call([])
        assert image_url is None


class TestPrivateReproduceSkipsSyncWithoutRemoteStorage:
    """Test that reproduce endpoint skips sync when remote storage unavailable."""

    @pytest.mark.asyncio
    async def test_skips_sync_when_remote_unavailable(self):
        """When remote storage is not available, sync should be skipped."""
        from unittest.mock import AsyncMock, patch

        from studio.app.common.routers.dataview import private_reproduce_experiment

        mock_record = MagicMock()

        with patch(
            "studio.app.common.routers.dataview.DataviewService."
            "find_dataview_record",
            return_value=mock_record,
        ), patch(
            "studio.app.common.routers.dataview.RemoteSyncStatusFileUtil."
            "check_sync_status_unsynced",
            return_value=True,
        ), patch(
            "studio.app.common.routers.dataview."
            "RemoteStorageController.is_available",
            return_value=False,
        ), patch(
            "studio.app.common.routers.dataview._ensure_experiment_downloaded"
        ) as mock_download, patch(
            "studio.app.common.routers.dataview.reproduce_experiment",
            new_callable=AsyncMock,
        ):
            await private_reproduce_experiment(
                workspace_id="1", unique_id="test123", db=MagicMock()
            )

        mock_download.assert_not_called()
