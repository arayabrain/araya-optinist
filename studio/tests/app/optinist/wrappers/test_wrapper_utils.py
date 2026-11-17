import numpy as np
import pytest

from studio.app.common.dataclass import HeatMapData
from studio.app.optinist.wrappers.optinist.utils import nan_gaussian_1d, row_order_from


def test_nan_gaussian_keeps_nan_and_leaves_neighbours_untouched():
    out = nan_gaussian_1d(np.array([[1.0, 1.0, np.nan, 1.0, 1.0]]), 1.0, axis=1)

    assert np.isnan(out[0, 2])
    np.testing.assert_allclose(np.delete(out[0], 2), 1.0)


def test_nan_gaussian_normalises_at_the_edges():
    np.testing.assert_allclose(nan_gaussian_1d(np.full((2, 5), 3.0), 2.0, axis=1), 3.0)


def test_nan_gaussian_sigma_zero_is_identity():
    a = np.array([[1.0, np.nan, 2.0]])
    np.testing.assert_array_equal(nan_gaussian_1d(a, 0, axis=1), a)


def test_nan_gaussian_smooths_along_the_given_axis():
    a = np.zeros((5, 2))
    a[2, 0] = 1.0
    out = nan_gaussian_1d(a, 1.0, axis=0)
    assert out[1, 0] > 0 and out[3, 0] > 0
    np.testing.assert_allclose(out[:, 1], 0.0)
    np.testing.assert_allclose(out[:, 0].sum(), 1.0 / np.array([1.0])[0], rtol=0.2)


def test_row_order_from_positions_cells_by_the_reference_labels():
    reference = HeatMapData(np.zeros((3, 1)), index=[5, 1, 3])
    np.testing.assert_array_equal(
        row_order_from(reference, np.array([1, 3, 5])), [2, 0, 1]
    )


def test_row_order_from_rejects_a_non_heatmap():
    with pytest.raises(AssertionError, match="order_from must be a heatmap"):
        row_order_from(np.zeros((3, 1)), np.array([0]))
