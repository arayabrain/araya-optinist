import numpy as np

from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass.base import BaseData
from studio.app.common.dataclass.heatmap import HeatMapData
from studio.app.optinist.dataclass.fluo import FluoData
from studio.app.optinist.wrappers.data_utils.data_utils_utils import return_as_data_type

CELL_NORMALIZATION_OPTIONS = ("none", "zscore")
MEAN_NORMALIZATION_OPTIONS = ("none", "zscore", "minmax")


def _option(params, key, options):
    value = (params.get(key, "none") if params else "none") or "none"
    value = str(value).strip().lower()
    if value not in options:
        raise ValueError(f"Unknown {key} '{value}'; expected one of {options}")
    return value


def _zscore(values, axis):
    center = np.nanmean(values, axis=axis, keepdims=True)
    denom = np.nanstd(values, axis=axis, keepdims=True)
    denom = np.where(denom > 0, denom, 1.0)
    return (values - center) / denom


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
                                 the entire dimension and log a warning.
                               - 'cell_normalization': 'none' or 'zscore'. Z-scores
                                 each row of an indexed input along its time axis
                                 before the mean is taken, so no single cell
                                 dominates mean_timeseries. Applied to sliced_data
                                 too; std and sem are dropped since their units
                                 no longer match.
                               - 'mean_normalization': Normalisation applied to the
                                 mean_timeseries output: 'none', 'zscore', or 'minmax'.

    Returns:
        dict: A dictionary containing the sliced data and any derived data products.
              mean_timeseries is only produced for indexed inputs (FluoData,
              BehaviorData, TimeSeriesData, BarData) whose index dimension is not
              collapsed by an integer slice spec. HeatMapData carries row labels in
              index, so it gets no mean_timeseries and no cell_normalization.
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

        # Parse slice notation (start:end or start:step:end)
        if isinstance(spec, str) and ":" in spec:
            try:
                parts = [p.strip() for p in spec.split(":")]

                if len(parts) == 2:
                    # Format: start:end
                    start = int(parts[0]) if parts[0] else None
                    end = int(parts[1]) if parts[1] else None
                    index_specs.append(slice(start, end))

                elif len(parts) == 3:
                    # Format: start:stop:step
                    start = int(parts[0]) if parts[0] else None
                    stop = int(parts[1]) if parts[1] else None
                    step = int(parts[2]) if parts[2] else None
                    index_specs.append(slice(start, stop, step))

                else:
                    logger.warning(f"Invalid slice format: {spec}")
                    index_specs.append(slice(None))

            except ValueError:
                logger.warning(f"Could not parse slice spec: {spec}")
                index_specs.append(slice(None))

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
        if hasattr(data, "index") and data.index is not None:
            original_index = data.index
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

        if cell_normalization == "zscore" and isinstance(data, HeatMapData):
            logger.warning("cell_normalization is not applied to HeatMapData")
        elif cell_normalization == "zscore" and eff_index_dim is not None:
            sliced_data = _zscore(sliced_data, axis=eff_index_dim)
            sliced_std = None
            sliced_sem = None

        # Create output filename
        file_name = (
            f"sliced_{data.file_name}" if hasattr(data, "file_name") else "sliced_data"
        )

        # Get mean timeseries based on the index dimension
        mean_timeseries = None
        if eff_index_dim is not None and not isinstance(data, HeatMapData):
            # Calculate mean over all dimensions except the index dimension
            other_axes = tuple(i for i in range(sliced_data.ndim) if i != eff_index_dim)

            if other_axes:  # Only if there are other dimensions to average over
                mean_timeseries = np.mean(sliced_data, axis=other_axes)
            else:
                # If only one dimension (the index dimension), use the data as is
                mean_timeseries = sliced_data

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
