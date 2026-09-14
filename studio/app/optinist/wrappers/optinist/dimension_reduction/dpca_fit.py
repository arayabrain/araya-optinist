import itertools

import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import HeatMapData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.utils import (
    recursive_flatten_params,
    standard_norm,
)

logger = AppLogger.get_logger()


def _as_list(value):
    return list(value) if isinstance(value, (list, tuple, np.ndarray)) else [value]


def calc_trigger(behavior_data, trigger_type, trigger_threshold):
    flg = np.array(
        np.asarray(behavior_data, dtype=float) > trigger_threshold, dtype=int
    )
    diff = np.ediff1d(flg)
    if trigger_type == "up":
        edges = diff == 1
    elif trigger_type == "down":
        edges = diff == -1
    elif trigger_type == "cross":
        edges = diff != 0
    else:
        raise ValueError(
            f"trigger_type must be 'up', 'down' or 'cross', got {trigger_type!r}"
        )
    return np.where(edges)[0] + 1


def build_trials(D, triggers, features, feature_columns, duration):
    """Reshape (time, unit) data into dPCA trial data.

    Returns trialX of shape (max_trials, unit, window, levels_1, levels_2, ...)
    padded with NaN where a condition has fewer trials, plus the level values
    of each feature.
    """
    d0, d1 = duration
    n_time, n_unit = D.shape
    triggers = np.asarray(triggers, dtype=int)
    inside = (triggers + d0 >= 0) & (triggers + d1 <= n_time)
    if not inside.all():
        logger.warning(
            "dropped %d of %d triggers whose window [%d, %d) leaves the recording",
            int((~inside).sum()),
            len(triggers),
            d0,
            d1,
        )
    if not inside.any():
        raise ValueError(
            f"no trigger window [{d0}, {d1}) fits inside the recording of "
            f"{n_time} frames; check trigger_duration and trigger_column"
        )
    triggers = triggers[inside]

    levels, codes = [], []
    for col, values in zip(feature_columns, features):
        uniq, inv = np.unique(np.asarray(values)[inside], return_inverse=True)
        if len(uniq) < 2:
            raise ValueError(
                f"feature column {col} has a single value ({uniq[0]}) at the "
                "triggers; a condition needs at least 2 levels"
            )
        if len(uniq) > 10 and len(uniq) > len(inv) / 2:
            raise ValueError(
                f"feature column {col} has {len(uniq)} distinct values over "
                f"{len(inv)} triggers; dPCA needs categorical conditions with a "
                "few levels, not a continuous variable"
            )
        levels.append(uniq)
        codes.append(inv)
    codes = np.stack(codes, axis=1)

    cond_shape = tuple(len(u) for u in levels)
    counts = np.zeros(cond_shape, dtype=int)
    np.add.at(counts, tuple(codes.T), 1)
    missing = np.argwhere(counts == 0)
    if len(missing):
        combos = ", ".join(
            "(" + ", ".join(str(levels[i][k]) for i, k in enumerate(m)) + ")"
            for m in missing
        )
        raise ValueError(
            f"no trials for feature level combination(s) {combos} of columns "
            f"{list(feature_columns)}; every combination needs at least one "
            "trigger, choose other feature_columns"
        )

    trialX = np.full((counts.max(), n_unit, d1 - d0) + cond_shape, np.nan)
    slot = np.zeros(cond_shape, dtype=int)
    for trig, code in zip(triggers, codes):
        idx = tuple(code)
        trialX[(slot[idx], slice(None), slice(None)) + idx] = D[trig + d0 : trig + d1].T
        slot[idx] += 1
    return trialX, levels


def prepare_inputs(X, B, iscell, params):
    """Validate params against the data and build the dPCA inputs."""
    if params["transpose"]:
        X = X.transpose()
    if X.shape[0] != B.shape[0]:
        raise ValueError(
            "neural_data and behaviors_data must share the time axis: neural "
            f"{X.shape}, behavior {B.shape}. Neural data must be (time, cells) "
            "after the transpose option is applied"
        )
    if iscell is not None:
        X = X[:, np.where(iscell > 0)[0]]

    trigger_column = int(params["trigger_column"])
    feature_columns = [int(c) for c in _as_list(params["feature_columns"])]
    for col in [trigger_column] + feature_columns:
        if not 0 <= col < B.shape[1]:
            raise ValueError(
                f"column {col} is out of range: behaviors_data has {B.shape[1]} "
                f"columns (0 to {B.shape[1] - 1})"
            )

    duration = [int(d) for d in _as_list(params["trigger_duration"])]
    if len(duration) != 2 or duration[0] >= duration[1]:
        raise ValueError(
            "trigger_duration must be [before, after] with before < after, "
            f"e.g. [-10, 10], got {params['trigger_duration']}"
        )

    labels = params["labels"]
    n_labels = 1 + len(feature_columns)
    if (
        not isinstance(labels, str)
        or len(labels) != n_labels
        or len(set(labels)) != n_labels
    ):
        raise ValueError(
            f"labels must be {n_labels} distinct characters (time plus one per "
            f"feature column {feature_columns}), got {labels!r}"
        )
    marginalizations = [
        "".join(c)
        for r in range(1, n_labels + 1)
        for c in itertools.combinations(labels, r)
    ]
    figure_features = [str(f) for f in _as_list(params["figure_features"])]
    bad = [f for f in figure_features if f not in marginalizations]
    if bad:
        raise ValueError(
            f"figure_features {bad} are not marginalizations of labels "
            f"{labels!r}; choose from {marginalizations}"
        )

    n_components = int(params["n_components"])
    if n_components > X.shape[1]:
        raise ValueError(
            f"n_components ({n_components}) exceeds the number of cells "
            f"({X.shape[1]})"
        )
    figure_components = [int(c) for c in _as_list(params["figure_components"])]
    bad = [c for c in figure_components if not 0 <= c < n_components]
    if bad:
        raise ValueError(
            f"figure_components {bad} must be between 0 and n_components - 1 "
            f"({n_components - 1})"
        )

    X = standard_norm(X, params["standard_mean"], params["standard_std"])

    triggers = calc_trigger(
        B[:, trigger_column], params["trigger_type"], params["trigger_threshold"]
    )
    if len(triggers) == 0:
        raise ValueError(
            f"no '{params['trigger_type']}' trigger found in behaviors_data column "
            f"{trigger_column} with threshold {params['trigger_threshold']}"
        )
    features = [B[triggers, c] for c in feature_columns]
    trialX, levels = build_trials(X, triggers, features, feature_columns, duration)

    return {
        "trialX": trialX,
        "levels": levels,
        "labels": labels,
        "n_components": n_components,
        "duration": duration,
        "figure_features": figure_features,
        "figure_components": figure_components,
    }


def dpca_fit(
    neural_data: FluoData,
    behaviors_data: BehaviorData,
    output_dir: str,
    iscell: IscellData = None,
    params: dict = None,
    **kwargs,
) -> dict():
    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start dpca: %s", function_id)

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    prepared = prepare_inputs(
        neural_data.data,
        behaviors_data.data,
        None if iscell is None else iscell.data,
        params,
    )
    trialX = prepared["trialX"]
    labels = prepared["labels"]

    # modules specific to function
    from dPCA import dPCA

    dpca = dPCA.dPCA(
        labels=labels,
        regularizer=params["regularizer"],
        n_components=prepared["n_components"],
        n_iter=params["n_iter"],
    )
    # ponytail: trialX only matters for regularizer='auto', unreachable from the UI
    result = dpca.fit_transform(np.nanmean(trialX, axis=0), trialX)

    d0, d1 = prepared["duration"]
    columns = list(range(d0, d1))
    info = {}
    for feat in prepared["figure_features"]:
        for comp in prepared["figure_components"]:
            tp = result[feat][comp]
            name = f"{feat}-component{comp}"
            info[name] = HeatMapData(
                tp.reshape(tp.shape[0], -1).T, columns=columns, file_name=name
            )

    postprocess = dict(result)
    for key, ratio in dpca.explained_variance_ratio_.items():
        postprocess[f"explained_variance_ratio_{key}"] = np.asarray(ratio)
    for label, values in zip(labels[1:], prepared["levels"]):
        postprocess[f"levels_{label}"] = np.asarray(values)
    info["nwbfile"] = {NWBDATASET.POSTPROCESS: {function_id: postprocess}}

    return info
