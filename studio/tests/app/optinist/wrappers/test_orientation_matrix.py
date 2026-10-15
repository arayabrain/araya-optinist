"""Orientation regression matrix: one canonical recording, fed in every
orientation the node toggles can correct, through every node that depends on
an axis order. Each row computes its expected numbers once with numpy from the
canonical arrays and every orientation must reproduce them; outputs are always
in the contract orientation of their type, and time-series-like outputs keep
the preview contract (one record per non-time row, time index as x)."""

import importlib
from pathlib import Path

import numpy as np
import pytest
import yaml

from studio.app.common.dataclass import (
    BarData,
    CsvData,
    HeatMapData,
    ImageData,
    ScatterData,
    TimeSeriesData,
)
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.data_utils.data_concat import data_concat
from studio.app.optinist.wrappers.data_utils.data_slice import data_slice
from studio.app.optinist.wrappers.data_utils.data_transpose import data_transpose
from studio.app.optinist.wrappers.data_utils.fluo_from_hdf5 import fluo_from_hdf5
from studio.app.optinist.wrappers.data_utils.roi_fluo_from_hdf5 import (
    roi_fluo_from_hdf5,
)
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.condition_split import (  # noqa: E501
    condition_split,
)
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.covariate_binning import (  # noqa: E501
    covariate_binning,
)
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.eta import ETA
from studio.app.optinist.wrappers.optinist.neural_population_analysis.correlation import (  # noqa: E501
    correlation,
)
from studio.app.optinist.wrappers.optinist.neural_population_analysis.cross_correlation import (  # noqa: E501
    cross_correlation,
)
from studio.tests.app.optinist.wrappers.test_dataclass_preview_contract import (
    assert_preview,
)

WRAPPERS = Path(__file__).resolve().parents[4] / "app/optinist/wrappers/optinist"
N_CELL, N_TIME = 4, 120
RNG = np.random.default_rng(42)

# (cells, time) neural activity and a (time, columns) behaviour table:
# position ramp, event pulses of 3 samples, trial number
FLUO = RNG.random((N_CELL, N_TIME))
EVENTS = np.zeros(N_TIME)
for _start in (20, 50, 80):
    EVENTS[_start : _start + 3] = 1.0
BEHAV = np.column_stack(
    [np.linspace(0.0, 1.0, N_TIME), EVENTS, np.repeat(np.arange(6.0), N_TIME // 6)]
)


def orientations(flip_behaviour=True):
    """(neural array, behaviour array, I/O toggles) for the contract layout and
    every flip the node toggles correct."""
    pairs = [(False, False), (True, False)]
    if flip_behaviour:
        pairs += [(False, True), (True, True)]
    for neural_flipped, behaviour_flipped in pairs:
        yield pytest.param(
            FLUO.T if neural_flipped else FLUO,
            BEHAV.T if behaviour_flipped else BEHAV,
            {"transpose_x": not neural_flipped, "transpose_y": behaviour_flipped},
            id=f"neural-{'time-major' if neural_flipped else 'contract'}-"
            f"behaviour-{'column-major' if behaviour_flipped else 'contract'}",
        )


def _defaults(name, io):
    params = yaml.safe_load((WRAPPERS / name).read_text())
    params["I/O"].update(io)
    return params


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


def _minmax(rows):
    low = rows.min(axis=1, keepdims=True)
    span = rows.max(axis=1, keepdims=True) - low
    return (rows - low) / np.where(span == 0, 1, span)


# C1, C2 ----------------------------------------------------------------------


def _fluo_inputs():
    plain = FluoData(FLUO.T)
    nwb = FluoData(FLUO.copy())
    nwb.nwb_oriented = True
    loader_transposed = FluoData(FLUO.copy())  # the input Transpose was applied
    loader_transposed.nwb_oriented = True
    return {"plain": plain, "nwb": nwb, "input-transpose": loader_transposed}


@pytest.mark.parametrize("kind", ["plain", "nwb", "input-transpose"])
def test_c1_fluo_from_hdf5_comes_out_roi_time_once(kind, output_dir):
    out = fluo_from_hdf5(_fluo_inputs()[kind], output_dir, params={"transpose": True})

    np.testing.assert_array_equal(out["fluorescence"].data, FLUO)


@pytest.mark.parametrize("kind", ["plain", "nwb", "input-transpose"])
def test_c2_roi_fluo_from_hdf5_comes_out_roi_time_once(kind, output_dir):
    cell_img = ImageData(
        np.zeros((N_CELL, 4, 4), dtype=np.float32), output_dir=output_dir
    )

    out = roi_fluo_from_hdf5(
        cell_img, _fluo_inputs()[kind], output_dir, params={"transpose": True}
    )

    np.testing.assert_array_equal(out["fluorescence"].data, FLUO)


# C3, C4, C5 -------------------------------------------------------------------

TIME_SLICE = slice(10, 50)


@pytest.mark.parametrize(
    "spec, expected, mean",
    [
        ([":", "10:50"], FLUO[:, TIME_SLICE], FLUO[:, TIME_SLICE].mean(axis=0)),
        (["1", ":"], FLUO[1][np.newaxis], FLUO[1]),
    ],
    ids=["time-slice", "integer-cell"],
)
def test_c3_slice_fluo(spec, expected, mean, output_dir):
    fluo = FluoData(FLUO, index=np.arange(N_TIME), file_name="f")

    out = data_slice(fluo, output_dir, params={"slice_dims": spec})

    np.testing.assert_allclose(np.atleast_2d(out["sliced_data"].data), expected)
    np.testing.assert_allclose(out["mean_timeseries"].data[0], mean)
    assert len(out["mean_timeseries"].index) == expected.shape[1]


def test_c3_slice_fluo_normalisations(output_dir):
    fluo = FluoData(FLUO, index=np.arange(N_TIME), file_name="f")
    window = FLUO[:, TIME_SLICE]
    zscored = (window - window.mean(axis=1, keepdims=True)) / window.std(
        axis=1, keepdims=True
    )
    mean = zscored.mean(axis=0)

    out = data_slice(
        fluo,
        output_dir,
        params={
            "slice_dims": [":", "10:50"],
            "cell_normalization": "zscore",
            "mean_normalization": "minmax",
        },
    )

    np.testing.assert_allclose(out["sliced_data"].data, zscored)
    np.testing.assert_allclose(
        out["mean_timeseries"].data[0], (mean - mean.min()) / np.ptp(mean)
    )
    assert list(out["sliced_data"].index) == list(range(10, 50))


@pytest.mark.parametrize(
    "spec, expected, index",
    [
        (["10:50", ":"], BEHAV[TIME_SLICE], range(10, 50)),
        ([":", "0:2"], BEHAV[:, 0:2], range(N_TIME)),
        ([":", "1"], BEHAV[:, [1]], range(N_TIME)),
    ],
    ids=["time-slice", "column-slice", "integer-column"],
)
def test_c4_slice_behaviour_on_axis_0(spec, expected, index, output_dir):
    behaviour = BehaviorData(BEHAV, file_name="b")

    out = data_slice(behaviour, output_dir, params={"slice_dims": spec})

    sliced = out["sliced_data"]
    assert type(sliced) is BehaviorData and "mean_timeseries" not in out
    np.testing.assert_array_equal(sliced.data, expected)
    sliced.save_json(output_dir)
    assert_preview(sliced, expected.T, index)


@pytest.mark.parametrize(
    "make, spec, expected_type, expected",
    [
        (
            lambda: CsvData(BEHAV, params={}),
            ["10:50", ":"],
            BehaviorData,
            BEHAV[TIME_SLICE],
        ),
        (
            lambda: TimeSeriesData(FLUO, file_name="t"),
            [":", "10:50"],
            TimeSeriesData,
            FLUO[:, TIME_SLICE],
        ),
        (
            lambda: HeatMapData(FLUO, file_name="h"),
            ["0:2", ":"],
            HeatMapData,
            FLUO[0:2],
        ),
        (lambda: BarData(FLUO, file_name="b"), ["0:2", ":"], BarData, FLUO[0:2]),
        (
            lambda: ScatterData(FLUO[:2].T, file_name="s"),
            [":", "0:10"],
            ScatterData,
            FLUO[:2, 0:10],
        ),
    ],
    ids=["csv", "timeseries", "heatmap", "bar", "scatter"],
)
def test_c5_slice_keeps_the_contract_type(
    make, spec, expected_type, expected, output_dir
):
    out = data_slice(make(), output_dir, params={"slice_dims": spec})["sliced_data"]

    assert type(out) is expected_type
    np.testing.assert_array_equal(out.data, expected)


# C6 --------------------------------------------------------------------------

TABLE_A, TABLE_B = BEHAV[:, :2], BEHAV[:, 2:]
TIME_A, TIME_B = BEHAV[:60], BEHAV[60:]


@pytest.mark.parametrize(
    "a, b, params, expected_type, expected, index",
    [
        (
            BehaviorData(TABLE_A),
            BehaviorData(TABLE_B),
            {},
            BehaviorData,
            BEHAV,
            range(N_TIME),
        ),
        (
            BehaviorData(TIME_A),
            BehaviorData(TIME_B),
            {"axis": 0},
            BehaviorData,
            BEHAV,
            range(N_TIME),
        ),
        (
            FluoData(FLUO[:2]),
            FluoData(FLUO[2:]),
            {},
            FluoData,
            FLUO,
            range(N_TIME),
        ),
        (
            FluoData(FLUO[:, :60]),
            FluoData(FLUO[:, 60:]),
            {"axis": 1},
            FluoData,
            FLUO,
            range(N_TIME),
        ),
        (
            IscellData(BEHAV[:, 0]),
            IscellData(BEHAV[:, 1]),
            {"axis": 1},
            BehaviorData,
            BEHAV[:, :2],
            range(N_TIME),
        ),
        (
            IscellData(np.ones(3)),
            IscellData(np.zeros(2)),
            {},
            IscellData,
            np.array([1.0, 1, 1, 0, 0]),
            None,
        ),
        (BarData(FLUO[:2]), BarData(FLUO[2:]), {}, BarData, FLUO, range(N_CELL)),
        (
            HeatMapData(FLUO[:2]),
            HeatMapData(FLUO[2:]),
            {},
            HeatMapData,
            FLUO,
            None,
        ),
        (
            ScatterData(FLUO[:2, :60].T),
            ScatterData(FLUO[:2, 60:].T),
            {},
            ScatterData,
            FLUO[:2],
            None,
        ),
    ],
    ids=[
        "behaviour-columns",
        "behaviour-time",
        "fluo-rows",
        "fluo-time",
        "1d-stack",
        "iscell",
        "bar",
        "heatmap",
        "scatter",
    ],
)
def test_c6_concat(a, b, params, expected_type, expected, index, output_dir):
    out = data_concat(a, b, output_dir, params=params)["concatenated_data"]

    assert type(out) is expected_type
    np.testing.assert_array_equal(out.data, expected)
    if index is not None:
        assert list(out.index) == list(index)
    if isinstance(out, TimeSeriesData):
        out.save_json(output_dir)
        rows = expected.T if isinstance(out, BehaviorData) else expected
        assert_preview(out, rows, index)


def test_c6_concat_std_follows_the_data_axis(output_dir):
    std_a, std_b = np.full(TABLE_A.shape, 0.1), np.full(TABLE_B.shape, 0.2)
    joined = data_concat(
        BehaviorData(TABLE_A, std=std_a),
        BehaviorData(TABLE_B, std=std_b),
        output_dir,
        params={},
    )
    computed = data_concat(
        FluoData(FLUO[:2]),
        FluoData(FLUO[2:]),
        output_dir,
        params={"std_method": "compute"},
    )

    np.testing.assert_array_equal(
        joined["concatenated_data"].std, np.hstack([std_a, std_b])
    )
    assert type(joined["concatenated_std"]) is BehaviorData
    joined["concatenated_std"].save_json(output_dir)
    assert joined["concatenated_std"].output_path.data_shape == [3, N_TIME]
    np.testing.assert_allclose(
        computed["concatenated_std"].data[:, 0], FLUO.std(axis=1)
    )


# C7 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make, expected_type",
    [
        (lambda: BehaviorData(BEHAV, index=np.arange(N_TIME) + 5), TimeSeriesData),
        (lambda: CsvData(BEHAV, params={}), TimeSeriesData),
        (lambda: FluoData(FLUO), FluoData),
        (lambda: TimeSeriesData(FLUO), TimeSeriesData),
        (lambda: HeatMapData(FLUO), HeatMapData),
        (lambda: BarData(FLUO), BarData),
    ],
    ids=["behaviour", "csv", "fluo", "timeseries", "heatmap", "bar"],
)
def test_c7_transpose_type(make, expected_type, output_dir):
    data = make()

    out = data_transpose(data, output_dir, params={})["transposed_data"]

    assert type(out) is expected_type
    np.testing.assert_array_equal(out.data, data.data.T)
    if isinstance(out, TimeSeriesData):
        out.save_json(output_dir)
        index = data.index if isinstance(data, BehaviorData) else out.index
        assert_preview(out, data.data.T, index)


def test_c7_transpose_keeps_image_and_scatter(output_dir):
    movie = RNG.random((5, 4, 3)).astype(np.float32)
    image = data_transpose(
        ImageData(movie, output_dir=output_dir),
        output_dir,
        params={"transpose_dims": "0, 2, 1"},
    )["transposed_data"]
    scatter = data_transpose(ScatterData(FLUO[:2].T), output_dir, params={})[
        "transposed_data"
    ]

    assert type(image) is ImageData
    np.testing.assert_array_equal(image.data, movie.transpose(0, 2, 1))
    assert type(scatter) is ScatterData
    np.testing.assert_array_equal(scatter.data, FLUO[:2].T)


# C8 --------------------------------------------------------------------------

SPLITS = {
    "greater": ({"event_col_index": 0, "threshold": 0.5}, BEHAV[:, 0] > 0.5),
    "modulo": (
        {"event_col_index": 2, "condition": "equal", "threshold": 1, "modulo": 2},
        BEHAV[:, 2] % 2 == 1,
    ),
    "margin": (
        {"event_col_index": 1, "condition": "less", "threshold": 0.5, "margin": 2},
        np.convolve(EVENTS, np.ones(5))[2 : 2 + N_TIME] == 0,
    ),
}


@pytest.mark.parametrize("split", SPLITS.values(), ids=SPLITS.keys())
@pytest.mark.parametrize("neural, behaviour, io", orientations())
def test_c8_condition_split(neural, behaviour, io, split, output_dir):
    params, keep = split
    defaults = _defaults("basic_neural_analysis/params/condition_split.yaml", io)
    defaults["condition_split"].update(
        {k: v for k, v in params.items() if k != "event_col_index"}
    )
    defaults["I/O"]["event_col_index"] = params["event_col_index"]

    out = condition_split(
        FluoData(neural), BehaviorData(behaviour), output_dir, params=defaults
    )

    np.testing.assert_array_equal(out["behaviors_data"].data, BEHAV[keep])
    kept_neural = FLUO[:, keep]
    np.testing.assert_array_equal(
        out["neural_data"].data,
        kept_neural if io["transpose_x"] else kept_neural.T,
    )
    out["behaviors_data"].save_json(output_dir)
    assert_preview(out["behaviors_data"], BEHAV[keep].T, range(keep.sum()))


# C9 --------------------------------------------------------------------------

N_BINS = 5


def _binned(cells):
    position = BEHAV[:, 0]
    edges = np.linspace(position.min(), position.max(), N_BINS + 1)
    bins = np.digitize(position, edges[1:-1])
    return np.stack([FLUO[cells][:, bins == b].mean(axis=1) for b in range(N_BINS)], 1)


@pytest.mark.parametrize(
    "iscell, order_from, cells",
    [
        (None, None, [0, 1, 2, 3]),
        (IscellData(np.array([1, 0, 1, 1])), None, [0, 2, 3]),
        (None, HeatMapData(np.zeros((N_CELL, 1)), index=[3, 1, 0, 2]), [0, 1, 2, 3]),
    ],
    ids=["defaults", "iscell", "order_from"],
)
@pytest.mark.parametrize("neural, behaviour, io", orientations())
def test_c9_covariate_binning(
    neural, behaviour, io, iscell, order_from, cells, output_dir
):
    params = _defaults("basic_neural_analysis/params/covariate_binning.yaml", io)
    params["I/O"]["event_col_index"] = 0
    params["covariate_binning"]["n_bins"] = N_BINS
    expected = _binned(cells)

    out = covariate_binning(
        FluoData(neural),
        BehaviorData(behaviour),
        output_dir,
        iscell=iscell,
        order_from=order_from,
        params=params,
    )

    np.testing.assert_allclose(out["mean"].data, expected)
    heatmap = out["mean_heatmap"]
    rows = {c: i for i, c in enumerate(cells)}
    for row, cell in zip(heatmap.data, heatmap.index):
        np.testing.assert_allclose(row, _minmax(expected)[rows[cell]])
    if order_from is not None:
        assert list(heatmap.index) == [3, 1, 0, 2]


# C10 -------------------------------------------------------------------------


@pytest.mark.parametrize("neural, behaviour, io", orientations())
def test_c10_eta(neural, behaviour, io, output_dir):
    params = _defaults("basic_neural_analysis/params/eta.yaml", io)
    windows = np.stack([FLUO[:, s - 10 : s + 13] for s in (20, 50, 80)])
    mean = windows.mean(axis=0)

    out = ETA(FluoData(neural), BehaviorData(behaviour), output_dir, params=params)

    np.testing.assert_allclose(out["mean"].data, mean)
    np.testing.assert_allclose(out["mean_trace"].data[0], mean.mean(axis=0))
    np.testing.assert_allclose(out["mean_heatmap"].data, _minmax(mean))


# C11 -------------------------------------------------------------------------

C11 = {
    "cca": ("dimension_reduction.cca", "CCA", "cca.yaml", "sklearn", True),
    "pca": ("dimension_reduction.pca", "PCA", "pca.yaml", "sklearn", False),
    "tsne": ("dimension_reduction.tsne", "TSNE", "tsne.yaml", "sklearn", False),
    "glm": ("neural_decoding.glm", "GLM", "glm.yaml", "statsmodels", True),
    "lda": ("neural_decoding.lda", "LDA", "lda.yaml", "sklearn", True),
    "svm": ("neural_decoding.svm", "SVM", "svm.yaml", "sklearn", True),
    "granger": (
        "neural_population_analysis.granger",
        "Granger",
        "granger.yaml",
        "statsmodels",
        False,
    ),
}


def _numbers(info):
    return {
        key: np.asarray(value.data, dtype=float)
        for key, value in info.items()
        if key != "nwbfile" and np.asarray(value.data).dtype.kind in "biuf"
    }


@pytest.mark.parametrize("node", C11.values(), ids=C11.keys())
def test_c11_results_do_not_depend_on_input_orientation(node, output_dir):
    module, function, yaml_name, library, uses_behaviour = node
    pytest.importorskip(library)
    run = getattr(
        importlib.import_module(f"studio.app.optinist.wrappers.optinist.{module}"),
        function,
    )
    package = module.split(".")[0]

    def results(neural_flipped, behaviour_flipped):
        params = yaml.safe_load((WRAPPERS / package / "params" / yaml_name).read_text())
        args = [FluoData(FLUO.T if neural_flipped else FLUO)]
        if uses_behaviour:
            params["I/O"].update(
                {"transpose_x": not neural_flipped, "transpose_y": behaviour_flipped}
            )
            args.append(BehaviorData(BEHAV.T if behaviour_flipped else BEHAV))
        else:
            params["I/O"]["transpose"] = not neural_flipped
        return _numbers(run(*args, output_dir, params=params))

    reference = results(False, False)
    if not all(
        np.allclose(reference[k], v, equal_nan=True)
        for k, v in results(False, False).items()
    ):
        pytest.skip(f"{function} is not deterministic on this input")
    flips = [(True, False)] + ([(False, True), (True, True)] if uses_behaviour else [])
    for flip in flips:
        for key, value in results(*flip).items():
            np.testing.assert_allclose(value, reference[key], equal_nan=True)


def test_c11_dpca_reads_time_major_neural_data_once(output_dir):
    pytest.importorskip("dPCA")
    from studio.app.optinist.wrappers.optinist.dimension_reduction.dpca_fit import (
        dpca_fit,
    )

    sample = WRAPPERS.parents[4] / "sample_data/tutorial/input"
    behaviour = np.loadtxt(sample / "sample_mouse2p_behavior.csv", delimiter=",")
    fluo = np.random.default_rng(0).random((30, len(behaviour)))
    params = yaml.safe_load(
        (WRAPPERS / "dimension_reduction/params/dpca.yaml").read_text()
    )

    contract = dpca_fit(
        FluoData(fluo), BehaviorData(behaviour), output_dir, params=params
    )
    params["I/O"]["transpose"] = False
    flipped = dpca_fit(
        FluoData(fluo.T), BehaviorData(behaviour), output_dir, params=params
    )

    for key, value in _numbers(contract).items():
        np.testing.assert_allclose(_numbers(flipped)[key], value)


# C12 -------------------------------------------------------------------------


def test_c12_correlation_reads_cells_time_by_default(output_dir):
    params = yaml.safe_load(
        (WRAPPERS / "neural_population_analysis/params/correlation.yaml").read_text()
    )
    expected = np.corrcoef(FLUO)
    np.fill_diagonal(expected, np.nan)

    out = correlation(FluoData(FLUO), output_dir, params=params)

    np.testing.assert_allclose(out["corr"].data, expected, equal_nan=True)


def test_c12_cross_correlation_pairs_cells_by_default(output_dir):
    params = yaml.safe_load(
        (
            WRAPPERS / "neural_population_analysis/params/cross_correlation.yaml"
        ).read_text()
    )
    params["shuffle_sample_number"] = 2

    out = cross_correlation(FluoData(FLUO.copy()), output_dir, params=params)

    pairs = sorted(k for k in out if k != "nwbfile" and not k.startswith("shuffle"))
    assert pairs == ["0-1", "0-2", "0-3", "1-2", "1-3", "2-3"]
