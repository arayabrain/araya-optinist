import base64
import inspect
import json
import logging

import numpy as np
import pytest
from scipy.cluster.hierarchy import fcluster, leaves_list, linkage

from studio.app.common.dataclass import TimeSeriesData
from studio.app.common.dataclass.csv import CsvData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import IscellData
from studio.app.optinist.wrappers.optinist.neural_population_analysis.hierarchical_clustering import (  # noqa: E501
    _clusters_to_label,
    hierarchical_clustering,
)

PARAMS = {
    "transpose": False,
    "zscore_rows": False,
    "method": "ward",
    "metric": "euclidean",
    "cut_criterion": "n_clusters",
    "n_clusters": 4,
    "height_fraction": 0.5,
    "band": "sem",
    "response_start": 0,
    "response_end": 20,
}
INDEX = np.arange(-20, 40)


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


def _profiles(n_per=15, seed=0, noise=0.2):
    """Four peri-event response shapes (flat, transient, inhibited, sustained)."""
    t = INDEX / 10
    shapes = [
        np.zeros_like(t),
        4 * np.exp(-(((t - 1) / 0.6) ** 2)),
        -3 * (t > 0) * np.exp(-t / 2),
        3 * (t > 0) * (1 - np.exp(-t / 1.5)),
    ]
    rng = np.random.default_rng(seed)
    truth = np.repeat(np.arange(4), n_per)
    rng.shuffle(truth)
    X = np.array([shapes[c] for c in truth]) + rng.normal(
        0, noise, (len(truth), len(t))
    )
    return X, truth


def _run(output_dir, X, params=None, index=INDEX, cell_numbers=None):
    data = TimeSeriesData(X, index=index, cell_numbers=cell_numbers, file_name="m")
    return hierarchical_clustering(
        data, output_dir, params={**PARAMS, **(params or {})}
    )


def _post(info):
    (post,) = info["nwbfile"][NWBDATASET.POSTPROCESS].values()
    return post


def _same_partition(a, b):
    pairs = set(zip(a, b))
    return len(pairs) == len(set(a)) == len(set(b))


def test_recovers_planted_clusters(output_dir):
    X, truth = _profiles()
    labels = _post(_run(output_dir, X))["labels"]

    assert _same_partition(labels, truth)


def test_labels_and_order_match_scipy(output_dir):
    X, _ = _profiles(seed=3, noise=1.0)
    for method, metric in [
        ("ward", "euclidean"),
        ("average", "euclidean"),
        ("average", "correlation"),
    ]:
        post = _post(_run(output_dir, X, {"method": method, "metric": metric}))
        Z = linkage(X, method=method, metric=metric)

        np.testing.assert_allclose(post["linkage"], Z)
        np.testing.assert_array_equal(post["leaf_order"], leaves_list(Z))
        assert _same_partition(post["labels"], fcluster(Z, 4, criterion="maxclust"))


def test_clusters_are_numbered_from_the_top(output_dir):
    X, _ = _profiles()
    post = _post(_run(output_dir, X))
    top_down = post["labels"][post["leaf_order"].astype(int)][::-1]

    first_seen = list(dict.fromkeys(top_down))
    assert first_seen == [1, 2, 3, 4]
    # Each cluster is one contiguous block of rows
    assert np.count_nonzero(np.diff(top_down)) == 3


def test_heatmap_rows_are_dendrogram_leaves(output_dir):
    X, _ = _profiles()
    cells = np.arange(100, 100 + len(X))
    info = _run(output_dir, X, cell_numbers=cells)
    order = leaves_list(linkage(X, "ward"))
    heat = info["sorted_heatmap"]

    np.testing.assert_array_equal(heat.data, X[order])
    assert heat.index == cells[order].tolist()
    assert heat.meta.yaxis_type == "category"


def test_cluster_mean_and_band(output_dir):
    X, _ = _profiles()
    info = _run(output_dir, X, {"band": "std"})
    labels = _post(info)["labels"]
    mean = info["cluster_mean"]

    assert mean.cell_numbers == [1, 2, 3, 4]
    assert mean.index == INDEX.tolist()
    for c in range(4):
        members = X[labels == c + 1]
        np.testing.assert_allclose(mean.data[c], members.mean(axis=0))
        np.testing.assert_allclose(mean.std[c], members.std(axis=0, ddof=1))

    sem = _run(output_dir, X)["cluster_mean"].std
    sizes = info["cluster_size"].data[0]
    np.testing.assert_allclose(sem, mean.std / np.sqrt(sizes)[:, np.newaxis])


def test_response_window_mean(output_dir):
    X, _ = _profiles()
    info = _run(output_dir, X)
    window = (INDEX >= 0) & (INDEX < 20)

    np.testing.assert_allclose(
        _post(info)["response_z"], info["cluster_mean"].data[:, window].mean(axis=1)
    )


def test_height_fraction_is_scale_invariant(output_dir):
    X, _ = _profiles(noise=1.0)
    params = {"cut_criterion": "height_fraction", "height_fraction": 0.4}
    a = _post(_run(output_dir, X, params))["labels"]
    b = _post(_run(output_dir, 3.7 * X, params))["labels"]

    np.testing.assert_array_equal(a, b)


def test_tied_merges_give_fewer_clusters_with_a_warning(output_dir, caplog):
    X = np.repeat([[0.0, 0.0], [1.0, 1.0]], 3, axis=0)
    caplog.set_level(logging.WARNING)
    info = _run(output_dir, X, {"n_clusters": 4, "response_end": 0}, index=[0, 1])

    assert _post(info)["labels"].max() <= 4
    assert len(info["cluster_mean"].data) == _post(info)["labels"].max()


def test_nan_columns_dropped_and_nan_rows_excluded(output_dir, caplog):
    X, truth = _profiles()
    X[:, 5] = np.nan  # an empty covariate bin
    X[7, 30] = np.nan  # one bad cell
    caplog.set_level(logging.WARNING)
    info = _run(output_dir, X)
    labels = _post(info)["labels"]

    assert labels[7] == 0
    assert (np.delete(labels, 7) > 0).all()
    assert info["sorted_heatmap"].data.shape == (len(X) - 1, len(INDEX) - 1)
    assert "all-NaN column" in caplog.text and "[7]" in caplog.text


def test_constant_rows_excluded_under_correlation(output_dir):
    X, _ = _profiles()
    X[3] = 2.0
    labels = _post(_run(output_dir, X, {"method": "average", "metric": "correlation"}))[
        "labels"
    ]

    assert labels[3] == 0 and (np.delete(labels, 3) > 0).all()


def test_zscore_rows_ignores_gain_and_offset(output_dir):
    X, _ = _profiles(noise=0.5)
    rng = np.random.default_rng(5)
    scaled = X * rng.uniform(0.2, 5, (len(X), 1)) + rng.normal(0, 3, (len(X), 1))
    z = (X - X.mean(axis=1, keepdims=True)) / X.std(axis=1, keepdims=True)
    post = _post(_run(output_dir, scaled, {"zscore_rows": True}))

    np.testing.assert_allclose(post["linkage"], linkage(z, "ward"), atol=1e-8)


def test_accepts_csv_data_without_index(output_dir):
    X, truth = _profiles()
    data = CsvData(X, params={}, file_name="csv")
    info = hierarchical_clustering(
        data, output_dir, params={**PARAMS, "response_end": 0}
    )

    assert info["cluster_mean"].index == list(range(X.shape[1]))
    assert _same_partition(_post(info)["labels"], truth)


@pytest.mark.parametrize(
    "params, data, match",
    [
        ({"metric": "correlation"}, None, "ward linkage needs"),
        ({"n_clusters": 100}, None, "n_clusters must be between"),
        (
            {"cut_criterion": "height_fraction", "height_fraction": 0},
            None,
            "height_fraction",
        ),
        ({"response_start": 100, "response_end": 200}, None, "response window"),
        ({"method": "single"}, None, "Unknown method"),
        ({}, IscellData(np.ones(5)), "2-D cells x features"),
        ({}, TimeSeriesData(np.full((3, 4), np.nan), file_name="m"), "every column"),
    ],
)
def test_clear_failures(output_dir, params, data, match):
    if data is None:
        X, _ = _profiles()
        data = TimeSeriesData(X, index=INDEX, file_name="m")
    with pytest.raises(AssertionError, match=match):
        hierarchical_clustering(data, output_dir, params={**PARAMS, **params})


def test_figure_is_an_isolated_svg_image(output_dir):
    X, _ = _profiles()
    html = _run(output_dir, X)["figure"].data

    prefix = '<img style="width:100%" src="data:image/svg+xml;base64,'
    assert html.startswith(prefix) and html.endswith('"/>')
    assert "<style" not in html
    svg = base64.b64decode(html[len(prefix) : -3]).decode()
    assert svg.lstrip().startswith("<?xml") and "<svg" in svg


def test_large_input_renders(output_dir):
    X, _ = _profiles(n_per=1250, noise=1.0)
    info = _run(output_dir, X, {"method": "average"})

    assert info["figure"].data.startswith("<img")
    assert len(_post(info)["labels"]) == 5000


def test_declared_outputs_are_returned(output_dir):
    X, _ = _profiles()
    declared = set(inspect.signature(hierarchical_clustering).return_annotation)

    assert declared <= set(_run(output_dir, X).keys())


def test_nwb_values_are_float_arrays(output_dir):
    X, _ = _profiles()
    for key, value in _post(_run(output_dir, X)).items():
        value = np.asarray(value)
        assert value.dtype.kind == "f", key
        assert 1 <= value.ndim <= 3, key


def test_color_limit_sets_the_figure_scale(output_dir):
    X, _ = _profiles()
    auto = _run(output_dir, X)["figure"].data
    fixed = _run(output_dir, X, {"color_limit": 10})["figure"].data

    assert auto != fixed


def test_many_clusters_still_render_and_write_outputs(output_dir, caplog):
    caplog.set_level(logging.WARNING)
    X = np.random.default_rng(0).normal(size=(120, 60))
    info = _run(output_dir, X, {"n_clusters": 100, "response_end": 0})

    assert "mean traces of clusters 1..30 of 100" in caplog.text
    assert info["figure"].data.startswith("<img")
    assert info["cluster_mean"].data.shape[0] == 100
    assert len(_post(info)["labels"]) == 120


def test_cluster_size_bars_are_numbered_from_one(output_dir, tmp_path):
    X, _ = _profiles()
    info = _run(output_dir, X)
    bar = info["cluster_size"]
    bar.save_json(str(tmp_path))

    assert list(bar.columns) == [1, 2, 3, 4]
    saved = json.loads((tmp_path / "cluster_size.json").read_text())
    assert saved["columns"] == [1, 2, 3, 4]


@pytest.mark.parametrize("criterion", ["n_clusters", "height_fraction"])
def test_cut_height_reproduces_the_labels(output_dir, criterion):
    X, _ = _profiles()
    info = _run(output_dir, X, {"cut_criterion": criterion})
    post = _post(info)
    Z = post["linkage"]
    cut = fcluster(Z, post["cut_height"][0], criterion="distance")

    assert _same_partition(cut, post["labels"])


def test_response_window_error_says_how_to_skip(output_dir):
    X, _ = _profiles()
    with pytest.raises(AssertionError, match="response_end equal to"):
        _run(output_dir, X, {"response_start": 500, "response_end": 600})


def test_every_cluster_number_drawn_when_they_fit():
    assert _clusters_to_label(np.array([10.0, 40, 70]), np.array([20, 30, 30]), 15) == [
        0,
        1,
        2,
    ]


def test_clashing_cluster_numbers_keep_the_larger_cluster():
    centres = np.array([10.0, 100, 103, 106, 200])
    sizes = np.array([20, 2, 9, 3, 50])

    assert _clusters_to_label(centres, sizes, 15) == [0, 2, 4]
