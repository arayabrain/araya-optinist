import warnings
from numbers import Integral, Real

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter1d, maximum_filter1d, minimum_filter1d

from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass.base import BaseData
from studio.app.common.dataclass.scatter import ScatterData
from studio.app.common.dataclass.timeseries import TimeSeriesData
from studio.app.optinist.dataclass.behavior import BehaviorData
from studio.app.optinist.dataclass.fluo import FluoData
from studio.app.optinist.wrappers.data_utils.data_utils_utils import return_as_data_type

CELL_NORMALIZATION_OPTIONS = ("none", "zscore", "dff")
MEAN_NORMALIZATION_OPTIONS = ("none", "zscore", "minmax")
F0_METHOD_OPTIONS = ("percentile", "maximin")


def _option(params, key, options, default="none"):
    value = (params.get(key, default) if params else default) or default
    value = str(value).strip().lower()
    if value not in options:
        raise ValueError(f"Unknown {key} '{value}'; expected one of {options}")
    return value


def _zscore(values, axis):
    if np.isnan(values).any():
        center = np.nanmean(values, axis=axis, keepdims=True)
        denom = np.nanstd(values, axis=axis, keepdims=True)
    else:
        center = values.mean(axis=axis, keepdims=True)
        denom = values.std(axis=axis, keepdims=True)
    denom = np.where(denom > 0, denom, 1.0)
    out = values - center
    out /= denom
    return out


def _dff_params(params):
    params = params or {}
    method = _option(params, "f0_method", F0_METHOD_OPTIONS, default="percentile")
    window = params.get("f0_window_frames", 0)
    percentile = params.get("f0_percentile", 8)
    sigma = params.get("f0_sigma_frames", 10)
    if isinstance(window, bool) or not isinstance(window, Integral) or window < 0:
        raise ValueError(f"f0_window_frames must be an integer >= 0, got {window!r}")
    if (
        isinstance(percentile, bool)
        or not isinstance(percentile, Real)
        or not 0 <= percentile <= 100
    ):
        raise ValueError(f"f0_percentile must be within 0..100, got {percentile!r}")
    if (
        isinstance(sigma, bool)
        or not isinstance(sigma, Real)
        or not 0 < sigma < float("inf")
    ):
        raise ValueError(f"f0_sigma_frames must be finite and > 0, got {sigma!r}")
    if method == "maximin" and window == 0:
        raise ValueError("maximin needs f0_window_frames > 0")
    return method, int(window), float(percentile), float(sigma)


def _dff(values, axis, method, window, percentile, sigma):
    """(x - F0) / |F0| per row along axis; F0 near 0 gives NaN."""
    logger = AppLogger.get_logger()
    dtype = np.result_type(values.dtype, np.float32)
    if values.size == 0:
        return np.asarray(values, dtype=dtype)
    x = np.moveaxis(np.asarray(values, dtype=dtype), axis, -1)
    rows = x.reshape(-1, x.shape[-1])
    n_time = rows.shape[-1]
    if window >= n_time:
        logger.warning(
            f"f0_window_frames {window} >= trace length {n_time}; using {n_time}"
        )
        window = n_time
    # SciPy sizes the Gaussian kernel from sigma before clipping it to the trace
    if method == "maximin" and sigma > n_time:
        logger.warning(
            f"f0_sigma_frames {sigma:g} > trace length {n_time}; using {n_time}"
        )
        sigma = float(n_time)
    finite = np.isfinite(rows)

    if method == "maximin":
        # Suite2p's dcnv.preprocess recipe, including its default reflect mode
        f0 = gaussian_filter1d(rows, sigma, axis=-1)
        minimum_filter1d(f0, window, axis=-1, output=f0)
        maximum_filter1d(f0, window, axis=-1, output=f0)
        for i in np.flatnonzero(~finite.all(axis=-1)):
            ok = finite[i]
            with np.errstate(invalid="ignore"):
                sub = gaussian_filter1d(np.where(ok, rows[i], 0), sigma)
                sub /= gaussian_filter1d(ok.astype(dtype), sigma)
            low = pd.Series(sub).rolling(window, center=True, min_periods=1).min()
            f0[i] = low.rolling(window, center=True, min_periods=1).max().to_numpy()
    elif 0 < window < n_time:
        half = window // 2
        # pandas rolls in float64, so rows go one at a time to bound memory
        f0 = np.empty_like(rows)
        for i, row in enumerate(rows):
            padded = pd.Series(np.pad(row, half, mode="edge"))
            rolled = padded.rolling(window, center=True, min_periods=1)
            f0[i] = rolled.quantile(percentile / 100).to_numpy()[half : half + n_time]
    else:
        # nanpercentile skips NaN but not inf; copy only when an inf is there
        source = np.where(finite, rows, np.nan) if np.isinf(rows).any() else rows
        f0 = np.nanpercentile(source, percentile, axis=-1, keepdims=True)
        f0 = f0.astype(dtype, copy=False)

    spread = np.max(rows, axis=-1, keepdims=True, initial=-np.inf, where=finite)
    spread -= np.min(rows, axis=-1, keepdims=True, initial=np.inf, where=finite)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = rows - f0
        out /= np.abs(f0, out=f0)
    zero = np.broadcast_to(f0 <= 1e-6 * spread, rows.shape)
    out[zero] = np.nan
    out[~finite] = np.nan
    n_zero = int(zero.any(axis=-1).sum())
    if n_zero:
        logger.warning(
            f"dff baseline F0 is near 0 in {n_zero} row(s); those samples are NaN"
        )
    return np.moveaxis(out.reshape(x.shape), -1, axis)


def _time_axis(data):
    """The axis that is time, by type. FluoData and TimeSeriesData are
    (rows, time); BehaviorData is time-major and Bar and HeatMap index rows,
    so they have none."""
    if isinstance(data, TimeSeriesData) and not isinstance(data, BehaviorData):
        return data.data.ndim - 1
    return None


def data_slice(
    data: BaseData,
    output_dir: str,
    params: dict = None,
    **kwargs,
) -> dict(sliced_data=BaseData, mean_timeseries=FluoData):
    """
    Slices data along specified dimensions.

    Parameters:
        data (BaseData): Input data to slice. Can be one of several types:
                         BehaviorData, CsvData, FluoData, ImageData, RoiData,
                         IscellData, TimeSeriesData, BarData, HeatMapData, or
                         ScatterData.
        output_dir (str): Directory to save the output data.
        params (dict, optional): Dictionary containing slice specifications:
                               - 'slice_dims': List of slice specs for each dimension.
                                 Each spec can be:
                                 - Null/empty/':'/all: Keep the entire dimension
                                 - 'start:stop': Range slice
                                 - 'start:stop:step': Strided slice
                                 - 'squeeze': Remove this dimension (must have size 1)
                                 - non-negative integer: Single index to select
                                   (removes dimension)
                                 Unparsable specs, 'squeeze' on a dimension of
                                 size > 1, and out-of-range integer indices keep
                                 the entire dimension and log a warning. A slice
                                 that selects no elements logs a warning, and a
                                 start:stop:step spec that appears to be in
                                 start:step:stop order logs a corrected hint.
                               - 'cell_normalization': 'none', 'zscore' or 'dff'.
                                 Z-scores, or converts to (F - F0) / |F0|, each
                                 row of an indexed input along its time axis
                                 before the mean is taken, so no single cell
                                 dominates mean_timeseries. Applied to sliced_data
                                 too; std and sem are dropped since their units
                                 no longer match.
                               - 'f0_method': dff baseline, 'percentile' or
                                 'maximin' (Suite2p: Gaussian of f0_sigma_frames,
                                 then min and max filters of f0_window_frames).
                               - 'f0_window_frames': dff sliding window in frames;
                                 0 = the whole recording (percentile only).
                               - 'f0_percentile': percentile for 'percentile'.
                               - 'f0_sigma_frames': Gaussian s.d. for 'maximin'.
                               - 'mean_normalization': Normalisation applied to the
                                 mean_timeseries output: 'none', 'zscore', or 'minmax'.

    Returns:
        dict: A dictionary containing the sliced data and any derived data products.
              mean_timeseries and cell_normalization apply only to inputs with a
              (rows, time) layout, FluoData and TimeSeriesData, whose time axis
              is not collapsed by an integer slice spec. BehaviorData is
              time-major, and BarData and HeatMapData index rows, so they get
              neither; asking for a normalisation on them logs a warning.
    """
    logger = AppLogger.get_logger()
    logger.info("Starting data slicing")

    # Get slice specifications from parameters
    slice_dims = params.get("slice_dims", None) if params else None
    cell_normalization = _option(
        params, "cell_normalization", CELL_NORMALIZATION_OPTIONS
    )
    mean_normalization = _option(
        params, "mean_normalization", MEAN_NORMALIZATION_OPTIONS
    )
    if cell_normalization == "dff":
        f0_method, f0_window, f0_percentile, f0_sigma = _dff_params(params)

    if slice_dims is None:
        slice_dims = []
    elif isinstance(slice_dims, str):
        slice_dims = [s.strip() for s in slice_dims.split(",")]
    elif isinstance(slice_dims, list):
        slice_dims = [s.strip() if isinstance(s, str) else s for s in slice_dims]

    try:
        raw_data = data.data
        ndim = raw_data.ndim
        original_shape = raw_data.shape
        logger.info(f"Original data shape: {original_shape}")
    except Exception as e:
        logger.error(f"Unable to access data: {str(e)}")
        raise ValueError(
            f"Input data doesn't have accessible .data attribute: {str(e)}"
        )

    # Make sure we have specs for all dimensions
    if len(slice_dims) < ndim:
        slice_dims = slice_dims + [None] * (ndim - len(slice_dims))
    elif len(slice_dims) > ndim:
        raise ValueError(
            f"Too many slice specs provided ({len(slice_dims)} for {ndim} dimensions)"
        )

    # Process each dimension's slice specification
    index_specs = []

    for i, spec in enumerate(slice_dims):
        # Skip empty specs or "all" indicator
        if spec is None or (isinstance(spec, str) and spec.strip() in ("", ":", "all")):
            index_specs.append(slice(None))
            continue

        # Handle "squeeze" keyword for dimensions of size 1
        if isinstance(spec, str) and spec.strip() == "squeeze":
            if raw_data.shape[i] == 1:
                index_specs.append(0)  # Integer index removes dimension
            else:
                logger.warning(
                    f"Cannot squeeze dimension {i} with size {raw_data.shape[i]}"
                )
                index_specs.append(slice(None))
            continue

        # Handle integer index (removes dimension)
        if isinstance(spec, int) or (isinstance(spec, str) and spec.strip().isdigit()):
            idx = int(spec.strip() if isinstance(spec, str) else spec)
            if 0 <= idx < raw_data.shape[i]:
                # Make sure we're adding an actual integer, not a slice
                index_specs.append(int(idx))  # Force conversion to int
            else:
                maxshape = raw_data.shape[i] - 1
                logger.warning(
                    f"Index {idx} out of bounds for dim {i} (max: {maxshape})"
                )
                index_specs.append(slice(None))
            continue

        # Parse slice notation (start:stop or start:stop:step)
        if isinstance(spec, str) and ":" in spec:
            parts = [p.strip() for p in spec.split(":")]
            try:
                if len(parts) == 2:
                    # Format: start:stop
                    start = int(parts[0]) if parts[0] else None
                    stop = int(parts[1]) if parts[1] else None
                    step = None

                elif len(parts) == 3:
                    # Format: start:stop:step
                    start = int(parts[0]) if parts[0] else None
                    stop = int(parts[1]) if parts[1] else None
                    step = int(parts[2]) if parts[2] else None

                else:
                    logger.warning(f"Invalid slice format: {spec}")
                    index_specs.append(slice(None))
                    continue

            except ValueError:
                logger.warning(f"Could not parse slice spec: {spec}")
                index_specs.append(slice(None))
                continue

            parsed = slice(start, stop, step)
            index_specs.append(parsed)

            dim_size = raw_data.shape[i]
            # step 0 is left to fail loudly when the slice is applied
            if step != 0:
                n_selected = len(range(*parsed.indices(dim_size)))
                hint = ""
                if len(parts) == 3 and all(parts) and n_selected <= 1 and stop != 0:
                    swapped = slice(start, step, stop)
                    if len(range(*swapped.indices(dim_size))) > max(n_selected, 1):
                        hint = (
                            "; the format is start:stop:step - did you mean "
                            f"'{parts[0]}:{parts[2]}:{parts[1]}'?"
                        )
                if (n_selected == 0 and dim_size > 0) or hint:
                    logger.warning(
                        f"Slice '{spec}' selects {n_selected} of "
                        f"{dim_size} elements on axis {i} (0-based){hint}"
                    )
            continue

        # Unrecognized specification
        logger.warning(f"Unrecognized slice spec: {spec}")
        index_specs.append(slice(None))

    logger.debug(f"Data slice - Applying indexing: {index_specs}")

    try:
        # Apply slices to all parts of data
        sliced_data = raw_data[tuple(index_specs)]
        logger.info(f"Sliced data shape: {sliced_data.shape}")

        # std and sem follow the data; both are assumed to share its shape
        sliced_std = None
        sliced_sem = None
        if getattr(data, "std", None) is not None:
            try:
                sliced_std = data.std[tuple(index_specs)]
            except Exception as std_err:
                logger.warning(f"Failed to slice std: {std_err}")
        if getattr(data, "sem", None) is not None:
            try:
                sliced_sem = data.sem[tuple(index_specs)]
            except Exception as sem_err:
                logger.warning(f"Failed to slice sem: {sem_err}")

        # Handle index
        sliced_index = None
        index_dim = None
        eff_index_dim = None
        time_dim = _time_axis(data)
        if hasattr(data, "index") and data.index is not None:
            original_index = data.index
            if time_dim is not None and len(original_index) == original_shape[time_dim]:
                index_dim = time_dim
            else:
                # Find which dimension matches the index length
                for dim_idx, dim_size in enumerate(original_shape):
                    if dim_size == len(original_index):
                        index_dim = dim_idx
                        break

            if index_dim is not None and isinstance(index_specs[index_dim], int):
                # Integer spec collapsed the index dimension; no index remains
                index_dim = None
            elif index_dim is not None:
                try:
                    # Apply the slice for the matching dimension
                    index_slice_spec = index_specs[index_dim]
                    sliced_index = original_index[index_slice_spec]
                except Exception as idx_err:
                    logger.warning(f"Failed to slice index: {idx_err}")
                    # Create default index for the sliced data
                    sliced_index = np.arange(
                        sliced_data.shape[-1]
                        if sliced_data.ndim > 1
                        else sliced_data.shape[0]
                    )
                # Position of the index axis after integer specs removed dimensions
                eff_index_dim = index_dim - sum(
                    1 for s in index_specs[:index_dim] if isinstance(s, int)
                )
            else:
                # No matching dimension found, create default index
                sliced_index = np.arange(
                    sliced_data.shape[-1]
                    if sliced_data.ndim > 1
                    else sliced_data.shape[0]
                )

        # Row labels follow the non-index axis of a 2D timeseries
        sliced_cell_numbers = None
        cell_numbers = getattr(data, "cell_numbers", None)
        if cell_numbers is not None and ndim == 2 and index_dim is not None:
            cell_dim = 1 - index_dim
            if len(cell_numbers) == original_shape[cell_dim]:
                sliced_cell_numbers = np.atleast_1d(
                    np.asarray(cell_numbers)[index_specs[cell_dim]]
                )

        eff_time_dim = (
            eff_index_dim if time_dim is not None and index_dim == time_dim else None
        )
        if cell_normalization != "none" and eff_time_dim is None:
            logger.warning(
                f"cell_normalization is not applied to {type(data).__name__}: "
                "it has no (rows, time) layout, or the time axis was sliced away"
            )
        elif cell_normalization != "none":
            if cell_normalization == "zscore":
                sliced_data = _zscore(sliced_data, axis=eff_time_dim)
            else:
                sliced_data = _dff(
                    sliced_data,
                    eff_time_dim,
                    f0_method,
                    f0_window,
                    f0_percentile,
                    f0_sigma,
                )
            sliced_std = None
            sliced_sem = None

        # Create output filename
        file_name = (
            f"sliced_{data.file_name}" if hasattr(data, "file_name") else "sliced_data"
        )

        # Mean over every axis but time
        mean_timeseries = None
        if eff_time_dim is not None:
            other_axes = tuple(i for i in range(sliced_data.ndim) if i != eff_time_dim)
            if other_axes and cell_normalization == "dff" and sliced_data.size:
                rows = np.moveaxis(sliced_data, eff_time_dim, -1)
                rows = rows.reshape(-1, rows.shape[-1])
                empty = np.isnan(rows).all(axis=-1)
                if empty.any():
                    logger.warning(
                        f"mean_timeseries leaves out {int(empty.sum())} "
                        "all-NaN dff row(s)"
                    )
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    mean_timeseries = np.nanmean(rows, axis=0)
            elif other_axes:
                mean_timeseries = np.mean(sliced_data, axis=other_axes)
            else:
                mean_timeseries = sliced_data.copy()
        elif mean_normalization != "none":
            logger.warning(
                f"mean_normalization is not applied: {type(data).__name__} "
                "produces no mean_timeseries"
            )

        if isinstance(data, ScatterData) and sliced_data.ndim < 2:
            logger.warning("ScatterData sliced below 2D no longer plots as a scatter")

        if mean_timeseries is not None and mean_normalization != "none":
            if not np.isfinite(mean_timeseries).any():
                logger.warning("mean_timeseries is entirely non-finite; not normalized")
            elif mean_normalization == "zscore":
                mean_timeseries = _zscore(mean_timeseries, axis=None)
            else:  # minmax
                low = np.nanmin(mean_timeseries)
                span = np.nanmax(mean_timeseries) - low
                mean_timeseries = (mean_timeseries - low) / (span if span > 0 else 1.0)

        # Create sliced data object using return_as_data_type with kwargs
        output_data = return_as_data_type(
            data,
            sliced_data,
            output_dir,
            file_name,
            std=sliced_std,
            sem=sliced_sem,
            index=sliced_index,
            cell_numbers=sliced_cell_numbers,
            output_type=None,
        )

        # Build the result dictionary with the main output
        result = {"sliced_data": list(output_data.values())[0]}
        if mean_timeseries is not None:
            result["mean_timeseries"] = FluoData(
                data=mean_timeseries, file_name="mean_timeseries", index=sliced_index
            )

        return result

    except Exception as e:
        logger.error(f"Error during data slicing: {str(e)}")
        raise ValueError(f"Data slicing failed: {str(e)}")
