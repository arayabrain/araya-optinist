import numpy as np
from scipy.stats import mode

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import HeatMapData, TimeSeriesData
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()


def calc_trigger(behavior_data, trigger_type, trigger_threshold):
    behavior_data = np.array(behavior_data, dtype=float)
    flg = np.array(behavior_data > trigger_threshold, dtype=int)
    trigger_idx = []
    trigger_lengths = []

    def find_trigger_length(index, flag_value):
        length = 0
        while index < len(flg) and flg[index] == flag_value:
            index += 1  # from initial trigger find subsequent end of trigger
            length += 1  # record the length of each trigger
        return length

    i = 0
    while i < len(flg):
        if (
            (trigger_type == "up" and flg[i] == 1 and (i == 0 or flg[i - 1] == 0))
            or (trigger_type == "down" and flg[i] == 0 and i > 0 and flg[i - 1] == 1)
            or (
                trigger_type == "cross"
                and (
                    (flg[i] == 1 and (i == 0 or flg[i - 1] == 0))
                    or (flg[i] == 0 and i > 0 and flg[i - 1] == 1)
                )
            )
        ):
            length = find_trigger_length(i, flg[i])
            trigger_idx.append(i)
            trigger_lengths.append(length)
            i += length
        else:
            i += 1
    num_detected = len(trigger_lengths)
    if num_detected == 0:
        return np.array([], dtype=int), 0, 0

    # Up and down runs differ in length, so cross aligns each edge with a fixed window
    if trigger_type == "cross":
        return np.array(trigger_idx), 0, num_detected

    mode_result = mode(trigger_lengths)  # find most common length
    trigger_len = mode_result.mode  # If scalar, use this value
    if hasattr(trigger_len, "__len__"):  # If array use first value
        trigger_len = trigger_len[0]

    trigger_idx = [
        idx
        for idx, length in zip(trigger_idx, trigger_lengths)
        if length == trigger_len  # if trigger_len is different (boundary cut-offs)
    ]  # don't include
    return np.array(trigger_idx), trigger_len, num_detected


def calc_trigger_average(neural_data, trigger_idx, trigger_len, pre_event, post_event):
    num_frame = neural_data.shape[0]
    event_trigger_data = []
    for idx in trigger_idx:
        event_start = idx - abs(pre_event)  # use abs to make sure neg value
        event_end = idx + trigger_len + post_event
        if event_end <= num_frame and event_start >= 0:
            event_trigger_data.append(neural_data[event_start:event_end])
    # Convert to numpy array (num_event, event_time, cell_number)
    event_trigger_data = np.array(event_trigger_data)
    return event_trigger_data


def ETA(
    neural_data: FluoData,
    behaviors_data: BehaviorData,
    output_dir: str,
    iscell: IscellData = None,
    params: dict = None,
    **kwargs,
) -> dict(mean=TimeSeriesData):
    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start ETA: %s", function_id)

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    neural_data = neural_data.data
    behaviors_data = behaviors_data.data

    if params["transpose_x"]:
        X = neural_data.transpose()
    else:
        X = neural_data

    if params["transpose_y"]:
        Y = behaviors_data.transpose()
    else:
        Y = behaviors_data

    assert (
        X.shape[0] == Y.shape[0]
    ), f"""
        neural_data and behaviors_data is not same dimension,
        neural.shape{X.shape}, behavior.shape{Y.shape}"""

    if iscell is not None:
        iscell = iscell.data
        ind = np.where(iscell > 0)[0]
        assert len(ind) > 0, "iscell marks no ROI as a cell, nothing to average"
        cell_numbers = ind
        X = X[:, ind]

    Y = Y[:, params["event_col_index"]]

    # The window is abs(pre_event) frames before the trigger whichever sign is given
    pre_event = -abs(params["pre_event"])
    post_event = params["post_event"]

    # calculate Triggers
    trigger_idxs, trigger_len, num_detected = calc_trigger(
        Y, params["trigger_type"], params["trigger_threshold"]
    )
    assert num_detected > 0, (
        f"No triggers found: no {params['trigger_type']} crossing of "
        f"{params['trigger_threshold']} in behaviour column {params['event_col_index']}"
    )

    # post_event counts from the end of the trigger, so it may be negative
    window_len = abs(pre_event) + trigger_len + post_event
    assert window_len > 0, (
        f"Empty window: abs(pre_event) {abs(pre_event)} + trigger_len {trigger_len}"
        f" + post_event {post_event} must be > 0"
    )

    # calculate Triggered average
    event_trigger_data = calc_trigger_average(
        X,
        trigger_idxs,
        trigger_len,
        pre_event,
        post_event,
    )

    num_event = len(event_trigger_data)
    dropped_length = num_detected - len(trigger_idxs)
    dropped_edge = len(trigger_idxs) - num_event
    if dropped_length or dropped_edge:
        reasons = []
        if dropped_length:
            reasons.append(
                f"{dropped_length} dropped, length differs from the modal {trigger_len}"
            )
        if dropped_edge:
            reasons.append(f"{dropped_edge} dropped, window crosses the recording edge")
        logger.warning(
            "ETA: averaged %d of %d triggers (%s)",
            num_event,
            num_detected,
            "; ".join(reasons),
        )
    assert num_event > 0, f"All {num_detected} triggers were dropped, see the log"

    # (cell_number, event_time_lambda)
    mean = np.mean(event_trigger_data, axis=0)
    # Sample std; undefined with one event rather than a false zero
    std = (
        np.std(event_trigger_data, axis=0, ddof=1)
        if num_event > 1
        else np.full(mean.shape, np.nan)
    )
    sem = std / np.sqrt(num_event)
    mean = mean.transpose()
    std = std.transpose()
    sem = sem.transpose()

    nwbfile = {}
    nwbfile[NWBDATASET.POSTPROCESS] = {
        function_id: {
            "mean": mean,
            "std": std,
            "sem": sem,
            "num_sample": [num_event],
        }
    }
    min_value = np.min(mean, axis=1, keepdims=True)
    value_range = np.max(mean, axis=1, keepdims=True) - min_value
    value_range[value_range == 0] = 1  # a flat cell becomes a zero row, not NaN
    norm_mean = (mean - min_value) / value_range

    info = {}
    info["mean"] = TimeSeriesData(
        mean,
        std=std,
        sem=sem,
        index=list(range(pre_event, post_event + trigger_len)),
        cell_numbers=cell_numbers if iscell is not None else None,
        file_name="mean",
    )
    info["mean_heatmap"] = HeatMapData(
        norm_mean,
        columns=list(np.arange(pre_event, post_event + trigger_len)),
        index=list(cell_numbers) if iscell is not None else None,
        file_name="mean_heatmap",
        meta=PlotMetaData(yaxis_type="category"),
    )
    info["nwbfile"] = nwbfile
    return info
