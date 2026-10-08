import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.optinist.dataclass import BehaviorData, FluoData
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def condition_split(
    neural_data: FluoData,
    behaviors_data: BehaviorData,
    output_dir: str,
    params: dict = None,
    **kwargs,
) -> dict(neural_data=FluoData, behaviors_data=BehaviorData):
    """
    Keep only the time samples where one behaviour column meets a condition.

    A sample is kept when its value v in column event_col_index satisfies
    'greater' v > threshold, 'less' v < threshold, 'equal' v == threshold
    (exact) or 'between' threshold <= v <= threshold_upper. NaN never matches.
    With margin > 0, a kept sample within margin samples of a rejected one is
    dropped too, which trims transitions where the behaviour stream and the
    imaging frames are not aligned to the sample.
    Kept samples are concatenated in their original order and both outputs keep
    the input orientation. Nothing else is filtered: there is no trial, run or
    modal-length inference, and a downstream window can span the join between
    two kept segments that were not adjacent in the recording.
    """
    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start condition_split: %s", function_id)

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

    values = np.asarray(Y[:, col], dtype=float)
    condition = params["condition"]
    threshold = float(params["threshold"])
    if condition == "greater":
        keep = values > threshold
    elif condition == "less":
        keep = values < threshold
    elif condition == "equal":
        keep = values == threshold
    elif condition == "between":
        upper = float(params["threshold_upper"])
        assert (
            upper >= threshold
        ), f"threshold_upper {upper} must be >= threshold {threshold} for 'between'"
        keep = (values >= threshold) & (values <= upper)
    else:
        raise ValueError(
            f"condition must be one of greater, less, equal, between, got {condition}"
        )

    margin = int(params.get("margin", 0))
    assert margin >= 0, f"margin must be >= 0, got {margin}"
    if margin:
        window = np.ones(2 * margin + 1)
        near = np.convolve((~keep).astype(int), window)[margin : margin + len(keep)]
        keep &= near == 0

    assert keep.any(), (
        f"No sample of behaviour column {col} matches condition {condition} "
        f"with threshold {threshold} and margin {margin}"
    )
    num_segment = np.count_nonzero(np.diff(keep.astype(int), prepend=0) == 1)
    logger.info(
        "condition_split: kept %d of %d samples in %d segment(s)",
        np.count_nonzero(keep),
        len(keep),
        num_segment,
    )

    X = X[keep]
    Y = Y[keep]
    return {
        "neural_data": FluoData(
            X.transpose() if params["transpose_x"] else X, file_name="neural_data"
        ),
        "behaviors_data": BehaviorData(
            Y.transpose() if params["transpose_y"] else Y, file_name="behaviors_data"
        ),
    }
