import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import HeatMapData, TimeSeriesData
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def covariate_binning(
    neural_data: FluoData,
    behaviors_data: BehaviorData,
    output_dir: str,
    iscell: IscellData = None,
    params: dict = None,
    **kwargs,
) -> dict(mean=TimeSeriesData, mean_heatmap=HeatMapData):
    """
    Mean activity per cell in equal-width bins of one behaviour column.

    Every time sample whose covariate lies in [bin_min, bin_max] is used: bin i
    holds bin_min + i * width <= value < bin_min + (i + 1) * width, and the last
    bin also holds value == bin_max. Samples outside the range, NaN or infinite
    are excluded. No run, trial or modal-length filtering is applied, so each
    bin's std and sem are over individual time samples, not trials.
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
    mean = np.full((num_cell, n_bins), np.nan)
    std = np.full((num_cell, n_bins), np.nan)
    num_sample = np.zeros(n_bins, dtype=int)
    for b in range(n_bins):
        samples = X[in_range & (bin_idx == b)]
        num_sample[b] = len(samples)
        if num_sample[b] > 0:
            mean[:, b] = samples.mean(axis=0)
        if num_sample[b] > 1:
            std[:, b] = samples.std(axis=0, ddof=1)
    sem = std / np.sqrt(np.maximum(num_sample, 1))

    if (num_sample == 0).any():
        logger.warning(
            "covariate_binning: %d of %d bins have no samples: %s",
            np.count_nonzero(num_sample == 0),
            n_bins,
            np.flatnonzero(num_sample == 0).tolist(),
        )

    min_value = np.nanmin(mean, axis=1, keepdims=True)
    value_range = np.nanmax(mean, axis=1, keepdims=True) - min_value
    value_range[value_range == 0] = 1
    norm_mean = (mean - min_value) / value_range

    order = np.arange(num_cell)
    if params["sort_by_peak"]:
        peak = np.argmax(np.nan_to_num(norm_mean, nan=-1.0), axis=1)
        order = np.argsort(peak, kind="stable")

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
        norm_mean[order],
        columns=centers.tolist(),
        index=cell_numbers[order].tolist(),
        file_name="mean_heatmap",
        meta=PlotMetaData(yaxis_type="category"),
    )
    info["nwbfile"] = nwbfile
    return info
