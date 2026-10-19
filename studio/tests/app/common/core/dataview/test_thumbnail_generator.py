"""Tests for ThumbnailGenerator HDF5/MAT thumbnail generation."""

import os

import h5py
import imageio.v3 as imageio
import numpy as np
import pytest
from scipy.io import savemat

from studio.app.common.core.dataview.dataview import DatasetPaths
from studio.app.common.core.dataview.thumbnail_generator import ThumbnailGenerator


@pytest.fixture
def output_path(tmp_path):
    return str(tmp_path / "thumb.png")


class TestRenderArrayAsThumbnail:
    def test_2d_array(self, output_path):
        arr = np.random.randint(0, 255, (64, 64), dtype=np.uint8)
        ThumbnailGenerator._render_array_as_thumbnail(arr, output_path)
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        assert img.ndim == 2
        assert img.shape == (64, 64)

    def test_large_array_resized(self, output_path):
        arr = np.random.rand(1024, 2048).astype(np.float32)
        ThumbnailGenerator._render_array_as_thumbnail(arr, output_path, max_size=256)
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        assert max(img.shape[:2]) <= 256

    @pytest.mark.parametrize("keep_aspect, dim", [(True, 2), (False, 255)])
    def test_only_matrices_are_scaled_by_percentile(
        self, output_path, keep_aspect, dim
    ):
        arr = np.ones((64, 64), dtype=np.float32)
        arr[:, 0] = 0.0
        arr[0, 1] = 100.0
        ThumbnailGenerator._render_array_as_thumbnail(
            arr, output_path, max_size=64, keep_aspect=keep_aspect
        )
        img = imageio.imread(output_path)
        assert img[10, 10] == dim

    def test_thin_matrix_keeps_at_least_one_pixel(self, output_path):
        # A tall, 2-wide matrix used to resize to a 0-pixel-wide PNG
        arr = np.random.rand(1025, 2).astype(np.float32)
        ThumbnailGenerator._render_array_as_thumbnail(arr, output_path, max_size=256)
        img = imageio.imread(output_path)
        assert img.shape == (256, 1)

    def test_sparse_events_are_not_flattened_to_grey(self, output_path):
        arr = np.zeros((64, 64), dtype=np.float32)
        arr[10, 10] = 5.0
        ThumbnailGenerator._render_array_as_thumbnail(arr, output_path)
        img = imageio.imread(output_path)
        assert img.max() == 255 and img.min() == 0

    def test_matrix_without_aspect_fills_a_square(self, output_path):
        arr = np.random.rand(600, 40).astype(np.float32)
        ThumbnailGenerator._render_array_as_thumbnail(
            arr, output_path, max_size=256, keep_aspect=False
        )
        img = imageio.imread(output_path)
        assert img.shape == (256, 256)

    def test_sparse_matrix_keeps_its_events_when_downsampled(self, output_path):
        arr = np.zeros((2000, 300), dtype=np.float32)
        arr[::100, ::50] = 1.0  # 20 x 6 events, invisible to nearest-neighbour
        ThumbnailGenerator._render_array_as_thumbnail(
            arr, output_path, max_size=128, keep_aspect=False
        )
        img = imageio.imread(output_path)
        assert (img == 255).sum() >= 100

    def test_dense_noise_is_not_speckled_by_the_downsampling(self, output_path):
        arr = np.random.default_rng(0).random((2000, 300)).astype(np.float32)
        ThumbnailGenerator._render_array_as_thumbnail(
            arr, output_path, max_size=128, keep_aspect=False
        )
        img = imageio.imread(output_path)
        assert min((img <= 10).mean(), (img >= 245).mean()) < 0.01

    def test_symmetric_noise_pools_the_same_way_whatever_the_seed(self, output_path):
        bright = set()
        for seed in range(30):
            arr = np.random.default_rng(seed).random((2000, 3)).astype(np.float32)
            ThumbnailGenerator._render_array_as_thumbnail(
                arr, output_path, max_size=128, keep_aspect=False
            )
            bright.add(imageio.imread(output_path).mean() > 127)
        assert len(bright) == 1

    def test_sparse_negative_events_survive_the_downsampling(self, output_path):
        arr = np.zeros((2000, 300), dtype=np.float32)
        arr[::100, ::50] = -1.0
        ThumbnailGenerator._render_array_as_thumbnail(
            arr, output_path, max_size=128, keep_aspect=False
        )
        img = imageio.imread(output_path)
        assert (img == 0).sum() >= 100 and img.max() == 255

    @pytest.mark.filterwarnings("error::RuntimeWarning")
    def test_nan_rows_render_as_background(self, output_path):
        arr = np.zeros((2000, 300), dtype=np.float32)
        arr[::100, ::50] = -1.0
        arr[500:510] = np.nan
        ThumbnailGenerator._render_array_as_thumbnail(
            arr, output_path, max_size=128, keep_aspect=False
        )
        img = imageio.imread(output_path)
        assert not (img == 0).all(axis=1).any()


class TestGenerateHdf5Thumbnail:
    def test_3d_dataset(self, tmp_path, output_path):
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("images", data=np.random.rand(10, 64, 64))
        ThumbnailGenerator.generate_hdf5_thumbnail(h5_path, output_path, "/images")
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        assert img.ndim == 2

    def test_2d_dataset(self, tmp_path, output_path):
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("matrix", data=np.random.rand(100, 50))
        ThumbnailGenerator.generate_hdf5_thumbnail(
            h5_path, output_path, "/matrix", max_size=64
        )
        img = imageio.imread(output_path)
        assert img.shape == (64, 64)

    def test_1d_dataset_raises(self, tmp_path, output_path):
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("vector", data=np.random.rand(100))
        with pytest.raises(ValueError, match="unsupported dimensionality"):
            ThumbnailGenerator.generate_hdf5_thumbnail(h5_path, output_path, "/vector")

    def test_invalid_path_raises(self, tmp_path, output_path):
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("data", data=np.random.rand(10, 10))
        with pytest.raises(KeyError):
            ThumbnailGenerator.generate_hdf5_thumbnail(
                h5_path, output_path, "/nonexistent"
            )

    def test_nested_hdf5_path(self, tmp_path, output_path):
        """Nested dataset path like '/data/images' works correctly."""
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            grp = f.create_group("data")
            grp.create_dataset("images", data=np.random.rand(5, 32, 32))
        ThumbnailGenerator.generate_hdf5_thumbnail(h5_path, output_path, "/data/images")
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        assert img.ndim == 2


class TestGenerateMatThumbnail:
    def test_2d_dataset(self, tmp_path, output_path):
        mat_path_file = str(tmp_path / "test.mat")
        savemat(mat_path_file, {"data": np.random.rand(100, 50)})
        ThumbnailGenerator.generate_mat_thumbnail(
            mat_path_file, output_path, "data", max_size=64
        )
        img = imageio.imread(output_path)
        assert img.shape == (64, 64)

    def test_3d_dataset(self, tmp_path, output_path):
        mat_path_file = str(tmp_path / "test.mat")
        savemat(mat_path_file, {"images": np.random.rand(5, 32, 32)})
        ThumbnailGenerator.generate_mat_thumbnail(mat_path_file, output_path, "images")
        assert os.path.exists(output_path)

    def test_1d_dataset_raises(self, tmp_path, output_path):
        mat_path_file = str(tmp_path / "test.mat")
        savemat(mat_path_file, {"vec": np.random.rand(100)})
        with pytest.raises(ValueError, match="unsupported dimensionality"):
            ThumbnailGenerator.generate_mat_thumbnail(mat_path_file, output_path, "vec")

    def test_nested_mat_path(self, tmp_path, output_path):
        """Nested path like 'data/behavior' works (matches tutorial4 pattern)."""
        mat_path_file = str(tmp_path / "test.mat")
        savemat(mat_path_file, {"data": {"behavior": np.random.rand(50, 30)}})
        ThumbnailGenerator.generate_mat_thumbnail(
            mat_path_file, output_path, "data/behavior"
        )
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        assert img.ndim == 2


class TestGenerateInputThumbnailDispatch:
    def test_hdf5_path_none_gives_placeholder(self, tmp_path, output_path):
        """Without hdf5_path, non-TIFF files get a placeholder."""
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("data", data=np.random.rand(10, 10))
        ThumbnailGenerator.generate_input_thumbnail(
            source_path=h5_path,
            output_path=output_path,
            abs_source_path=h5_path,
        )
        assert os.path.exists(output_path)
        # Placeholder is RGB (3 channels)
        img = imageio.imread(output_path)
        assert img.ndim == 3

    def test_hdf5_path_provided_gives_real_thumbnail(self, tmp_path, output_path):
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("images", data=np.random.rand(5, 32, 32))
        ThumbnailGenerator.generate_input_thumbnail(
            source_path=h5_path,
            output_path=output_path,
            abs_source_path=h5_path,
            dataset_paths=DatasetPaths(hdf5_path="/images"),
        )
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        # Real thumbnail is grayscale (2D)
        assert img.ndim == 2

    def test_hdf5_bad_path_falls_back_to_placeholder(self, tmp_path, output_path):
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("data", data=np.random.rand(10, 10))
        ThumbnailGenerator.generate_input_thumbnail(
            source_path=h5_path,
            output_path=output_path,
            abs_source_path=h5_path,
            dataset_paths=DatasetPaths(hdf5_path="/nonexistent"),
        )
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        assert img.ndim == 3  # placeholder is RGB

    def test_mat_path_provided_gives_real_thumbnail(self, tmp_path, output_path):
        mat_file = str(tmp_path / "test.mat")
        savemat(mat_file, {"matrix": np.random.rand(40, 40)})
        ThumbnailGenerator.generate_input_thumbnail(
            source_path=mat_file,
            output_path=output_path,
            abs_source_path=mat_file,
            dataset_paths=DatasetPaths(mat_path="matrix"),
        )
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        assert img.ndim == 2

    def test_both_paths_provided_hdf5_wins(self, tmp_path, output_path):
        """When both hdf5_path and mat_path are set, HDF5 branch is tried first."""
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("images", data=np.random.rand(5, 32, 32))
        ThumbnailGenerator.generate_input_thumbnail(
            source_path=h5_path,
            output_path=output_path,
            abs_source_path=h5_path,
            dataset_paths=DatasetPaths(hdf5_path="/images", mat_path="ignored"),
        )
        assert os.path.exists(output_path)
        img = imageio.imread(output_path)
        assert img.ndim == 2  # real thumbnail, not placeholder


class TestEdgeCases:
    def test_4d_hdf5_dataset(self, tmp_path):
        """4D dataset: first slice is 3D, _render_array_as_thumbnail handles it."""
        output = str(tmp_path / "thumb.png")
        h5_path = str(tmp_path / "test.h5")
        with h5py.File(h5_path, "w") as f:
            f.create_dataset("data", data=np.random.rand(3, 10, 10, 3))
        ThumbnailGenerator.generate_hdf5_thumbnail(h5_path, output, "/data")
        assert os.path.exists(output)
        img = imageio.imread(output)
        assert img.ndim == 2

    def test_mat_bad_path_falls_back_to_placeholder(self, tmp_path):
        """Invalid MAT dataset path falls back to placeholder."""
        output = str(tmp_path / "thumb.png")
        mat_file = str(tmp_path / "test.mat")
        savemat(mat_file, {"data": np.random.rand(10, 10)})
        ThumbnailGenerator.generate_input_thumbnail(
            source_path=mat_file,
            output_path=output,
            abs_source_path=mat_file,
            dataset_paths=DatasetPaths(mat_path="nonexistent"),
        )
        assert os.path.exists(output)
        img = imageio.imread(output)
        assert img.ndim == 3  # placeholder is RGB
