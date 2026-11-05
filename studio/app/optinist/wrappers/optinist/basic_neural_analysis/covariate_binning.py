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
# median share of the bin range a trial id must span to count as one
MIN_TRIAL_COVERAGE = 0.25


def _not_a_trial_id(trial, covariate, in_range, lo, hi):
    """Why this behaviour column cannot be a trial id, or None if it can. A trial id
    is a whole number held over one stretch of frames, and a typical trial spans
    most of the binned range of the covariate."""
    finite = np.isfinite(trial)
    if not (in_range & finite).any():
        return "no in-range sample has a finite value"
    values = trial[finite]
    whole = values == np.round(values)
    if not whole.all() and whole.mean() >= 0.5:
        return (
            f"{np.count_nonzero(~whole):,} of its values are not whole numbers, as "
            f"when trial ids are interpolated onto the imaging frames; round them to "
            f"the nearest trial first"
        )
    # over all frames, not only the binned ones, so a narrow bin range keeps laps
    seen = finite & np.isfinite(covariate)
    ids, pos = np.unique(trial[seen], return_inverse=True)
    clipped = np.clip(covariate[seen], lo, hi)
    low = np.full(len(ids), np.inf)
    high = np.full(len(ids), -np.inf)
    np.minimum.at(low, pos, clipped)
    np.maximum.at(high, pos, clipped)
    coverage = float(np.median((high - low) / (hi - lo)))
    if coverage < MIN_TRIAL_COVERAGE:
        return (
            f"a typical value spans {coverage:.0%} of the binned range {lo:g} to "
            f"{hi:g}, where a trial spans most of it (a continuous signal, a frame "
            f"counter or a clock does not)"
        )
    run_ids = values[np.r_[True, np.diff(values) != 0]]
    ids, n_runs = np.unique(run_ids, return_counts=True)
    recurring = ids[n_runs > 1]
    if len(recurring) == 1:
        return (
            f"value {recurring[0]:g} recurs in {n_runs.max():,} separate stretches "
            f"of frames; if it marks the frames between trials, drop them with "
            f"condition_split first"
        )
    if len(recurring) > 1:
        return (
            f"its {len(ids):,} values recur in {len(run_ids):,} separate stretches "
            f"of frames"
        )
    return None


def _check_trial_col(Y, trial_col, event_col, in_range, lo, hi, n_bins):
    covariate = Y[:, event_col].astype(float)
    trial = Y[:, trial_col].astype(float)
    problem = _not_a_trial_id(trial, covariate, in_range, lo, hi)
    if problem is None:
        n_trial = len(np.unique(trial[in_range & np.isfinite(trial)]))
        n_kept = np.count_nonzero(in_range & np.isfinite(trial))
        if n_trial * n_bins > n_kept:
            logger.warning(
                "covariate_binning: %d trials x %d bins is more than the %d binned "
                "samples, so most trial-bins are empty; fewer bins (n_bins) fill them",
                n_trial,
                n_bins,
                n_kept,
            )
        return
    candidates = [
        c
        for c in range(Y.shape[1])
        if c not in (trial_col, event_col)
        and _not_a_trial_id(Y[:, c].astype(float), covariate, in_range, lo, hi) is None
        and np.nanmax(Y[in_range, c]) > np.nanmin(Y[in_range, c])
    ]
    if len(candidates) == 1:
        hint = f"Column {candidates[0]} looks like one."
    elif candidates:
        hint = f"Columns {', '.join(map(str, candidates))} look like one."
    else:
        hint = "No other column looks like one; -1 averages time samples instead."
    raise AssertionError(
        f"trial_col_index {trial_col} does not look like a trial id: {problem}. "
        f"A trial column repeats one id over each trial's frames "
        f"(1, 1, 1, 2, 2, ...). {hint}"
    )


def _trial_stats(X, in_range, bin_idx, trial, n_bins, min_samples):
    """Mean per trial per bin, then mean, sample std, sem and trial count across
    trials. A NaN sample drops that trial's bin for the cell, and its sem counts that;
    num_sample is the occupancy, shared by the cells."""
    keep = in_range & np.isfinite(trial)
    trial_ids, trial_pos = np.unique(trial[keep], return_inverse=True)
    n_trial = len(trial_ids)
    group = trial_pos * n_bins + bin_idx[keep]
    counts = np.bincount(group, minlength=n_trial * n_bins).reshape(n_trial, n_bins)
    # a NaN sample makes that trial's bin NaN for that cell, as in the sample mode
    per_trial = np.empty((X.shape[1], n_trial * n_bins))
    for c in range(X.shape[1]):
        per_trial[c] = np.bincount(
            group, weights=X[keep, c], minlength=n_trial * n_bins
        )
    per_trial = per_trial.reshape(X.shape[1], n_trial, n_bins)
    with np.errstate(invalid="ignore", divide="ignore"):
        per_trial /= counts
    sparse = (counts > 0) & (counts < min_samples)
    if sparse.any():
        logger.warning(
            "covariate_binning: %d of %d trial-bins with samples have fewer than "
            "min_samples %d and are NaN",
            np.count_nonzero(sparse),
            np.count_nonzero(counts),
            min_samples,
        )
    per_trial[:, counts < min_samples] = np.nan
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # a bin with no trial is NaN
        mean = np.nanmean(per_trial, axis=1)
        std = np.nanstd(per_trial, axis=1, ddof=1)
    n_valid = np.sum(~np.isnan(per_trial), axis=1)
    std[n_valid < 2] = np.nan
    sem = std / np.sqrt(np.maximum(n_valid, 1))
    num_sample = (counts >= min_samples).sum(axis=0)
    return mean, std, sem, num_sample, n_trial


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
    assert col == int(col) and 0 <= col < Y.shape[1], (
        f"event_col_index {col} is out of range for behaviour data with "
        f"{Y.shape[1]} column(s); it must be a whole number from 0 to "
        f"{Y.shape[1] - 1}"
    )
    col = int(col)
    n_bins = int(params["n_bins"])
    assert n_bins > 0, f"n_bins must be > 0, got {n_bins}"
    normalization = str(params.get("heatmap_normalization") or "minmax").strip().lower()
    assert normalization in HEATMAP_NORMALIZATION_OPTIONS, (
        f"heatmap_normalization must be one of {HEATMAP_NORMALIZATION_OPTIONS}, "
        f"got '{normalization}'"
    )
    trial_col = params.get("trial_col_index")
    trial_col = -1 if trial_col is None else trial_col
    assert trial_col == int(trial_col) and -1 <= trial_col < Y.shape[1], (
        f"trial_col_index {trial_col} must be -1 (off) or a column index from 0 to "
        f"{Y.shape[1] - 1}"
    )
    trial_col = int(trial_col)
    min_samples = params.get("min_samples")
    min_samples = 1 if min_samples is None else min_samples
    assert (
        min_samples == int(min_samples) and min_samples >= 1
    ), f"min_samples must be a whole number >= 1, got {min_samples}"
    min_samples = int(min_samples)
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
        _check_trial_col(Y, trial_col, col, in_range, lo, hi, n_bins)
        mean, std, sem, num_sample, n_trial = _trial_stats(
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
        counts = np.zeros(n_bins, dtype=int)
        for b in range(n_bins):
            samples = X[in_range & (bin_idx == b)]
            counts[b] = len(samples)
            if counts[b] >= min_samples:
                mean[:, b] = samples.mean(axis=0)
            if counts[b] > 1 and counts[b] >= min_samples:
                std[:, b] = samples.std(axis=0, ddof=1)
        sem = std / np.sqrt(np.maximum(counts, 1))
        sparse = (counts > 0) & (counts < min_samples)
        if sparse.any():
            logger.warning(
                "covariate_binning: %d of %d bins have fewer than min_samples %d "
                "and are NaN: %s",
                np.count_nonzero(sparse),
                n_bins,
                min_samples,
                np.flatnonzero(sparse).tolist(),
            )
        num_sample = counts

    if (num_sample == 0).any():
        logger.warning(
            "covariate_binning: %d of %d bins have no samples: %s",
            np.count_nonzero(num_sample == 0),
            n_bins,
            np.flatnonzero(num_sample == 0).tolist(),
        )
    if n_trial is None:
        # as in trial mode, num_sample counts only what the mean is over
        num_sample = np.where(sparse, 0, num_sample)

    smoothed = nan_gaussian_1d(mean, smooth_sigma, axis=1) if smooth_sigma > 0 else mean
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # an all-NaN row stays NaN
        min_value = np.nanmin(smoothed, axis=1, keepdims=True)
        value_range = np.nanmax(smoothed, axis=1, keepdims=True) - min_value
    value_range[value_range == 0] = 1
    norm_mean = (smoothed - min_value) / value_range

    if normalization == "mean":
        # masked sum and count, so the binned samples are not copied out of X
        binned = in_range
        if n_trial is not None:
            binned = in_range & np.isfinite(np.asarray(Y[:, trial_col], dtype=float))
        counted = binned[:, None] & ~np.isnan(X)
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
