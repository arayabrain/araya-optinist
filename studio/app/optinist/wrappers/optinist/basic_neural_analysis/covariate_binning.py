import warnings

import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import HeatMapData, TimeSeriesData
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.utils import (
    nan_gaussian_1d,
    peak_order,
    recursive_flatten_params,
    row_order_from,
)

logger = AppLogger.get_logger()

HEATMAP_NORMALIZATION_OPTIONS = ("minmax", "mean", "none")


def _trial_stats(X, in_range, bin_idx, trial, n_bins, min_samples):
    """Mean per trial per bin, then mean, sample std and trial count across trials."""
    keep = in_range & np.isfinite(trial)
    assert keep.any(), "no in-range sample has a finite trial id"
    trial_ids, trial_pos = np.unique(trial[keep], return_inverse=True)
    n_trial = len(trial_ids)
    group = trial_pos * n_bins + bin_idx[keep]
    counts = np.bincount(group, minlength=n_trial * n_bins).reshape(n_trial, n_bins)
    Xk = X[keep]
    # a NaN sample makes that trial's bin NaN for that cell, as in the sample mode
    sums = np.stack(
        [
            np.bincount(group, weights=Xk[:, c], minlength=n_trial * n_bins)
            for c in range(X.shape[1])
        ]
    ).reshape(X.shape[1], n_trial, n_bins)
    with np.errstate(invalid="ignore", divide="ignore"):
        per_trial = sums / counts
    per_trial[:, counts < min_samples] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a bin with no trial is NaN
        mean = np.nanmean(per_trial, axis=1)
        std = np.nanstd(per_trial, axis=1, ddof=1)
    std[np.sum(~np.isnan(per_trial), axis=1) < 2] = np.nan
    num_sample = (counts >= min_samples).sum(axis=0)
    return mean, std, num_sample, n_trial


def covariate_binning(
    neural_data: FluoData,
    behaviors_data: BehaviorData,
    output_dir: str,
    iscell: IscellData = None,
    order_from: HeatMapData = None,
    params: dict = None,
    **kwargs,
) -> dict(mean=TimeSeriesData, mean_heatmap=HeatMapData):
    """
    Mean activity per cell in equal-width bins of one behaviour column.

    Every time sample whose covariate lies in [bin_min, bin_max] is used: bin i
    holds bin_min + i * width <= value < bin_min + (i + 1) * width, and the last
    bin also holds value == bin_max. Samples outside the range, NaN or infinite
    are excluded. By default each bin's std and sem are over individual time
    samples; with trial_col_index >= 0 the samples are first averaged per trial
    (the behaviour column holding the trial id) and std, sem and num_sample are
    across trials. A bin (or trial-bin) with fewer than min_samples samples is
    NaN. smooth_sigma_bins > 0 smooths the heatmap along the bin axis with a
    Gaussian of that s.d. in bins; the mean output stays unsmoothed.

    When order_from is connected, the heatmap rows follow that heatmap's row
    labels (cell numbers) instead of their own peak, so two bins of the same
    cells, for example before and after a switch, share one row order.
    """
    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start covariate_binning: %s", function_id)

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    X = neural_data.data.transpose() if params["transpose_x"] else neural_data.data
    Y = (
        behaviors_data.data.transpose()
        if params["transpose_y"]
        else behaviors_data.data
    )
    if Y.ndim == 1:
        Y = Y[:, np.newaxis]

    assert X.shape[0] == Y.shape[0], (
        "neural_data and behaviors_data have a different number of time points, "
        f"neural.shape{X.shape}, behavior.shape{Y.shape}"
    )
    col = params["event_col_index"]
    assert 0 <= col < Y.shape[1], (
        f"event_col_index {col} is out of range for behaviour data with "
        f"{Y.shape[1]} column(s)"
    )
    n_bins = int(params["n_bins"])
    assert n_bins > 0, f"n_bins must be > 0, got {n_bins}"
    normalization = str(params.get("heatmap_normalization") or "minmax").strip().lower()
    assert normalization in HEATMAP_NORMALIZATION_OPTIONS, (
        f"heatmap_normalization must be one of {HEATMAP_NORMALIZATION_OPTIONS}, "
        f"got '{normalization}'"
    )
    trial_col = params.get("trial_col_index")
    trial_col = -1 if trial_col is None else int(trial_col)
    assert trial_col < Y.shape[1], (
        f"trial_col_index {trial_col} is out of range for behaviour data with "
        f"{Y.shape[1]} column(s)"
    )
    min_samples = params.get("min_samples")
    min_samples = 1 if min_samples is None else int(min_samples)
    assert min_samples >= 1, f"min_samples must be >= 1, got {min_samples}"
    smooth_sigma = float(params.get("smooth_sigma_bins") or 0)
    assert smooth_sigma >= 0, f"smooth_sigma_bins must be >= 0, got {smooth_sigma}"

    cell_numbers = np.arange(X.shape[1])
    if iscell is not None:
        assert len(iscell.data) == X.shape[1], (
            f"iscell has {len(iscell.data)} entries but neural_data has "
            f"{X.shape[1]} ROIs"
        )
        cell_numbers = np.where(iscell.data > 0)[0]
        assert len(cell_numbers) > 0, "iscell marks no ROI as a cell, nothing to bin"
        X = X[:, cell_numbers]

    covariate = np.asarray(Y[:, col], dtype=float)
    finite = np.isfinite(covariate)
    if params["use_data_range"]:
        assert finite.any(), f"behaviour column {col} has no finite values"
        lo, hi = covariate[finite].min(), covariate[finite].max()
    else:
        lo, hi = float(params["bin_min"]), float(params["bin_max"])
        assert np.isfinite([lo, hi]).all(), f"bin range must be finite: [{lo}, {hi}]"
    assert hi > lo, f"bin range is empty: min {lo} must be below max {hi}"

    edges = np.linspace(lo, hi, n_bins + 1)
    centers = np.round((edges[:-1] + edges[1:]) / 2, 10)
    in_range = (covariate >= lo) & (covariate <= hi)
    bin_idx = np.digitize(covariate, edges[1:-1])
    assert in_range.any(), f"no sample of behaviour column {col} lies in [{lo}, {hi}]"

    num_cell = X.shape[1]
    n_trial = None
    if trial_col >= 0:
        mean, std, num_sample, n_trial = _trial_stats(
            X,
            in_range,
            bin_idx,
            np.asarray(Y[:, trial_col], dtype=float),
            n_bins,
            min_samples,
        )
    else:
        mean = np.full((num_cell, n_bins), np.nan)
        std = np.full((num_cell, n_bins), np.nan)
        num_sample = np.zeros(n_bins, dtype=int)
        for b in range(n_bins):
            samples = X[in_range & (bin_idx == b)]
            num_sample[b] = len(samples)
            if num_sample[b] >= min_samples:
                mean[:, b] = samples.mean(axis=0)
            if num_sample[b] > 1 and num_sample[b] >= min_samples:
                std[:, b] = samples.std(axis=0, ddof=1)
    sem = std / np.sqrt(np.maximum(num_sample, 1))

    if (num_sample == 0).any():
        logger.warning(
            "covariate_binning: %d of %d bins have no samples: %s",
            np.count_nonzero(num_sample == 0),
            n_bins,
            np.flatnonzero(num_sample == 0).tolist(),
        )
    sparse = (num_sample > 0) & (num_sample < min_samples)
    if sparse.any():
        logger.warning(
            "covariate_binning: %d of %d bins have fewer than min_samples %d "
            "and are NaN: %s",
            np.count_nonzero(sparse),
            n_bins,
            min_samples,
            np.flatnonzero(sparse).tolist(),
        )

    smoothed = nan_gaussian_1d(mean, smooth_sigma, axis=1) if smooth_sigma > 0 else mean
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN row stays NaN
        min_value = np.nanmin(smoothed, axis=1, keepdims=True)
        value_range = np.nanmax(smoothed, axis=1, keepdims=True) - min_value
    value_range[value_range == 0] = 1
    norm_mean = (smoothed - min_value) / value_range

    if normalization == "mean":
        # masked sum and count, so the binned samples are not copied out of X
        counted = in_range[:, None] & ~np.isnan(X)
        with np.errstate(invalid="ignore", divide="ignore"):
            cell_mean = np.sum(X, axis=0, where=counted) / counted.sum(axis=0)
        undefined = ~(cell_mean > 0)
        if undefined.any():
            logger.warning(
                "covariate_binning: heatmap_normalization 'mean' needs a positive "
                "cell mean; blanking %d cells: %s",
                np.count_nonzero(undefined),
                cell_numbers[undefined].tolist(),
            )
        cell_mean = np.where(undefined, np.nan, cell_mean)
        heatmap = smoothed / cell_mean[:, np.newaxis]
    elif normalization == "none":
        heatmap = smoothed
    else:
        heatmap = norm_mean

    order = np.arange(num_cell)
    if order_from is not None:
        order = row_order_from(order_from, cell_numbers)
    elif params["sort_by_peak"]:
        order = peak_order(norm_mean)

    nwbfile = {}
    nwbfile[NWBDATASET.POSTPROCESS] = {
        function_id: {
            "mean": mean,
            "std": std,
            "sem": sem,
            "bin_centers": centers,
            "num_sample": num_sample,
        }
    }
    if smooth_sigma > 0:
        nwbfile[NWBDATASET.POSTPROCESS][function_id]["mean_smoothed"] = smoothed

    if n_trial is not None:
        title = (
            f"n = {n_trial} trials, {num_sample.min()} to {num_sample.max()} per bin"
        )
    else:
        title = f"{num_sample.min()} to {num_sample.max()} samples per bin"
    if smooth_sigma > 0:
        title += f", smoothed {smooth_sigma:g} bins"

    info = {}
    info["mean"] = TimeSeriesData(
        mean,
        std=std,
        sem=sem,
        index=centers.tolist(),
        cell_numbers=cell_numbers,
        file_name="mean",
    )
    info["mean_heatmap"] = HeatMapData(
        heatmap[order],
        columns=centers.tolist(),
        index=cell_numbers[order].tolist(),
        file_name="mean_heatmap",
        meta=PlotMetaData(title=title, yaxis_type="category"),
    )
    info["nwbfile"] = nwbfile
    return info
