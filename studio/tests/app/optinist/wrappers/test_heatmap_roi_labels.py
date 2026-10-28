"""Heatmap rows carry ROI numbers and ROI axes are hinted as categorical."""
import numpy as np
import pytest

from studio.app.common.core.utils.file_reader import JsonReader
from studio.app.common.dataclass import HeatMapData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import BehaviorData, FluoData, IscellData
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.eta import ETA
from studio.app.optinist.wrappers.optinist.neural_population_analysis.correlation import (  # noqa: E501
    correlation,
)

ISCELL = np.array([0, 1, 0, 1, 1, 0])
CELLS = [1, 3, 4]
ETA_PARAMS = {
    "transpose_x": True,
    "transpose_y": False,
    "event_col_index": 1,
    "trigger_type": "up",
    "trigger_threshold": 0.5,
    "pre_event": -5,
    "post_event": 5,
}


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


def _saved(heatmap: HeatMapData, json_dir):
    heatmap.save_json(json_dir)
    return JsonReader.read_as_output(heatmap.json_path)


EVENT_STARTS = range(20, 180, 30)


def _eta(output_dir, iscell=None, sort_by_peak=False, fluo=None):
    rng = np.random.default_rng(0)
    behavior = np.zeros((200, 2))
    for start in EVENT_STARTS:
        behavior[start : start + 3, 1] = 1.0
    return ETA(
        FluoData(
            rng.random((len(ISCELL), 200)) if fluo is None else fluo, file_name="f"
        ),
        BehaviorData(behavior, file_name="b"),
        output_dir,
        iscell=iscell,
        params={**ETA_PARAMS, "sort_by_peak": sort_by_peak},
    )


def test_heatmap_default_index_is_unchanged(tmp_path):
    out = _saved(HeatMapData(np.zeros((3, 2))), str(tmp_path))

    assert out.index == [0, 1, 2]
    assert out.meta.yaxis_type is None


def test_eta_heatmap_rows_are_roi_numbers_with_iscell(output_dir, tmp_path):
    info = _eta(output_dir, IscellData(ISCELL))
    out = _saved(info["mean_heatmap"], str(tmp_path))

    assert out.index == CELLS
    assert out.index == [int(k) for k in info["mean"].cell_numbers]
    assert out.meta.yaxis_type == "category"
    assert out.meta.xaxis_type is None


def test_sorted_eta_heatmap_saves_its_row_labels(output_dir, tmp_path):
    fluo = np.zeros((len(ISCELL), 200))
    for cell, lag in zip(CELLS, [4, 0, -3]):  # cell 4 peaks first, cell 1 last
        for start in EVENT_STARTS:
            fluo[cell, start + lag] = 1.0
    info = _eta(output_dir, IscellData(ISCELL), sort_by_peak=True, fluo=fluo)
    out = _saved(info["mean_heatmap"], str(tmp_path))

    assert out.index == [4, 3, 1]
    assert out.meta.yaxis_type == "category"


def test_eta_heatmap_rows_without_iscell_are_all_rois(output_dir, tmp_path):
    out = _saved(_eta(output_dir)["mean_heatmap"], str(tmp_path))

    assert out.index == list(range(len(ISCELL)))


def test_correlation_labels_both_axes_with_roi_numbers(output_dir, tmp_path):
    info = correlation(
        FluoData(np.random.default_rng(1).random((len(ISCELL), 40)), file_name="f"),
        output_dir,
        iscell=IscellData(ISCELL),
        params={"transpose": False},
    )
    out = _saved(info["corr"], str(tmp_path))

    assert out.index == CELLS
    assert [int(c) for c in out.columns] == CELLS
    assert (out.meta.xaxis_type, out.meta.yaxis_type) == ("category", "category")


def test_correlation_sort_by_cluster_permutes_rows_and_labels(output_dir):
    rng = np.random.default_rng(2)
    a, b = rng.normal(size=(2, 200))
    # Interleave two assemblies so the unsorted matrix is not block-diagonal
    X = np.array([(a if i % 2 else b) + 0.3 * rng.normal(size=200) for i in range(8)])
    X[5] = 1.0  # a constant cell sorts last
    plain = correlation(
        FluoData(X, file_name="f"), output_dir, params={"transpose": False}
    )
    info = correlation(
        FluoData(X, file_name="f"),
        output_dir,
        params={"transpose": False, "sort_by_cluster": True},
    )
    heat = info["corr"]
    order = np.asarray(heat.index)

    np.testing.assert_array_equal(heat.data, plain["corr"].data[np.ix_(order, order)])
    assert list(heat.columns) == list(order) and order[-1] == 5
    # Each assembly is one contiguous block
    assembly = order[:-1] % 2
    assert np.count_nonzero(np.diff(assembly)) == 1
    (post,) = info["nwbfile"][NWBDATASET.POSTPROCESS].values()
    np.testing.assert_array_equal(post["order"], order)
    np.testing.assert_array_equal(post["corr"], plain["corr"].data)


def test_correlation_sort_by_cluster_keeps_iscell_roi_numbers(output_dir):
    X = np.random.default_rng(3).random((len(ISCELL), 40))
    info = correlation(
        FluoData(X, file_name="f"),
        output_dir,
        iscell=IscellData(ISCELL),
        params={"transpose": False, "sort_by_cluster": True},
    )

    assert sorted(info["corr"].index) == CELLS
    assert list(info["corr"].columns) == list(info["corr"].index)
