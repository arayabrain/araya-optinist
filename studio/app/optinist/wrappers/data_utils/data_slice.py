import numpy as np

from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass.base import BaseData
from studio.app.common.dataclass.image import ImageData
from studio.app.optinist.dataclass.fluo import FluoData
from studio.app.optinist.wrappers.data_utils.data_utils_utils import return_as_data_type


def data_slice(
    data: BaseData,
    output_dir: str,
    params: dict = None,
    **kwargs,
) -> dict(sliced_data=BaseData):
    """
    Slices data along specified dimensions.

    Parameters:
        data (BaseData): Input data to slice. Can be one of several types:
                         BehaviorData, CsvData, FluoData, ImageData, RoiData,
                         or IscellData.
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

    Returns:
        dict: A dictionary containing the sliced data and any derived data products.
    """
    logger = AppLogger.get_logger()
    logger.info("Starting data slicing")

    # Get slice specifications from parameters
    slice_dims = params.get("slice_dims", None) if params else None

    if slice_dims is not None:
        if isinstance(slice_dims, str):
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

    # Handle case where no slice specs are provided
    if slice_dims is None:
        logger.debug("No slice specifications provided, returning original data")
        output_data = return_as_data_type(data, raw_data, output_dir, "sliced_data")
        return {"sliced_data": list(output_data.values())[0]}

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

        # Handle std if available
        sliced_std = None
        if hasattr(data, "std") and data.std is not None:
            try:
                # Apply the same slicing to std (assumes std has same shape as data)
                sliced_std = data.std[tuple(index_specs)]
            except Exception as std_err:
                logger.warning(f"Failed to slice std: {std_err}")
                sliced_std = None

        # Handle index
        sliced_index = None
        index_dim = None
        if hasattr(data, "index") and data.index is not None:
            original_index = data.index
            # Find which dimension matches the index length
            for dim_idx, dim_size in enumerate(original_shape):
                if dim_size == len(original_index):
                    index_dim = dim_idx
                    break

            if index_dim is not None:
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
            else:
                # No matching dimension found, create default index
                sliced_index = np.arange(
                    sliced_data.shape[-1]
                    if sliced_data.ndim > 1
                    else sliced_data.shape[0]
                )

        # Create output filename
        file_name = (
            f"sliced_{data.file_name}" if hasattr(data, "file_name") else "sliced_data"
        )

        # Initialize info dictionary
        info = {}

        # Get mean timeseries based on the index dimension
        mean_timeseries = None
        if sliced_index is not None and index_dim is not None:
            # Calculate mean over all dimensions except the index dimension
            other_axes = tuple(i for i in range(sliced_data.ndim) if i != index_dim)

            if other_axes:  # Only if there are other dimensions to average over
                mean_timeseries = np.mean(sliced_data, axis=other_axes)
            else:
                # If only one dimension (the index dimension), use the data as is
                mean_timeseries = sliced_data

        if mean_timeseries is not None:
            # Create a FluoData object for mean timeseries with the sliced index
            mean_ts_data = FluoData(
                data=mean_timeseries, file_name="mean_timeseries", index=sliced_index
            )
            info["mean_timeseries"] = mean_ts_data

        # Create mean image for multi-dimensional data
        if sliced_data.ndim >= 3 and index_dim is not None:
            # For 3D+ data, create a spatial average (average over the time dimension)
            spatial_axes = tuple(i for i in range(sliced_data.ndim) if i != index_dim)
            if len(spatial_axes) >= 2:
                mean_image = np.mean(sliced_data, axis=index_dim)
                mean_img_data = ImageData(
                    data=mean_image, output_dir=output_dir, file_name="mean_image"
                )
                info["mean_image"] = mean_img_data

        # Create sliced data object using return_as_data_type with kwargs
        output_data = return_as_data_type(
            data,
            sliced_data,
            output_dir,
            file_name,
            std=sliced_std,
            index=sliced_index,
            output_type=None,
        )
        info.update(output_data)

        main_data_object = list(output_data.values())[0]
        # Build the result dictionary with the main output
        result = {"sliced_data": main_data_object}
        # Add any additional outputs
        if "mean_timeseries" in info:
            result["mean_timeseries"] = info["mean_timeseries"]
        if "mean_image" in info:
            result["mean_image"] = info["mean_image"]

        return result

    except Exception as e:
        logger.error(f"Error during data slicing: {str(e)}")
        raise ValueError(f"Data slicing failed: {str(e)}")
