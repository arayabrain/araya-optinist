"""
Shared utility functions for Optinist wrappers.
"""

import numpy as np

__all__ = [
    "standard_norm",
    "recursive_flatten_params",
    "param_check",
    "peak_order",
    "row_order_from",
    "nan_gaussian_1d",
]


def standard_norm(X, mean, std):
    from sklearn.preprocessing import StandardScaler

    sc = StandardScaler(with_mean=mean, with_std=std)
    tX = sc.fit_transform(X)
    return tX


def recursive_flatten_params(params, result_params: dict, nest_counter=0):
    assert nest_counter <= 10, f"Nest depth overflow. [{nest_counter}]"
    nest_counter += 1

    for key, nested_param in params.items():
        if type(nested_param) is dict:
            recursive_flatten_params(nested_param, result_params, nest_counter)
        else:
            result_params[key] = nested_param


def param_check(params):
    for key in params:
        if (params[key] == "") or (params[key] == "None"):
            params[key] = None
    return params


def peak_order(norm_mean):
    """Row order by each row's peak column, earliest first; ties keep row order.

    NaN never wins the argmax, so an empty bin does not become a row's peak and
    an all-NaN row sorts first, with the flat rows.
    """
    peak = np.argmax(np.nan_to_num(norm_mean, nan=-1.0), axis=1)
    return np.argsort(peak, kind="stable")


def row_order_from(reference, cell_numbers: np.ndarray) -> np.ndarray:
    """Position of each of our cells in the reference heatmap's row labels."""
    from studio.app.common.dataclass import HeatMapData

    assert isinstance(
        reference, HeatMapData
    ), f"order_from must be a heatmap, got {type(reference).__name__}"
    labels = [] if reference.index is None else np.asarray(reference.index).tolist()
    assert (
        len(labels) > 0
    ), "order_from heatmap has no row labels to take the order from"
    position = {}
    for i, label in enumerate(labels):
        position.setdefault(label, i)
    cells = np.asarray(cell_numbers).tolist()
    missing = [c for c in cells if c not in position]
    assert not missing, (
        f"order_from heatmap has no row for {len(missing)} of {len(cells)} "
        f"cells (first missing: {missing[:5]}; its labels are "
        f"{type(labels[0]).__name__} such as {labels[:5]}); "
        "both nodes must bin the same cells"
    )
    return np.argsort([position[c] for c in cells], kind="stable")


def nan_gaussian_1d(a, sigma: float, axis: int = -1) -> np.ndarray:
    """Gaussian smoothing along one axis that skips NaN instead of spreading it.

    Each output is the kernel-weighted mean of the finite inputs it covers
    (normalised convolution), so a NaN sample leaves its neighbours intact and
    an edge is averaged over the samples that exist. NaN inputs stay NaN.
    """
    from scipy.ndimage import gaussian_filter1d

    a = np.asarray(a, dtype=float)
    if sigma <= 0:
        return a
    finite = np.isfinite(a)
    weight = gaussian_filter1d(finite.astype(float), sigma, axis=axis, mode="constant")
    total = gaussian_filter1d(
        np.where(finite, a, 0.0), sigma, axis=axis, mode="constant"
    )
    with np.errstate(invalid="ignore", divide="ignore"):
        out = total / weight
    out[~finite] = np.nan
    return out
