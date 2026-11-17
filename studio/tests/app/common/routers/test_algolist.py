import os
from collections import Counter

import pytest

from studio.app.common.core.snakemake.smk_utils import SmkInternalUtils, SmkUtils
from studio.app.common.routers import algolist
from studio.app.common.routers.algolist import NestDictGetter
from studio.app.common.schemas.algolist import Algo
from studio.app.wrappers import wrapper_dict
from studio.tests.app.common.loop_probe import Hold, health_while_held


def _collect_algos(nest_dict, algos):
    for value in nest_dict.values():
        if isinstance(value, Algo):
            algos[value.path] = value
        elif isinstance(value, dict) and "children" in value:
            _collect_algos(value["children"], algos)
    return algos


@pytest.mark.parametrize(
    "path, expected_returns",
    [
        ("utils/data_slice", ["sliced_data", "mean_timeseries"]),
        (
            "utils/roi_from_hdf5",
            ["iscell", "all_roi", "non_cell_roi", "cell_roi"],
        ),
        (
            "utils/roi_fluo_from_hdf5",
            ["iscell", "all_roi", "non_cell_roi", "cell_roi", "fluorescence"],
        ),
        (
            "optinist/basic_neural_analysis/eta",
            ["mean", "mean_trace", "mean_heatmap"],
        ),
        (
            "optinist/dimension_reduction/pca",
            [
                "explained_variance",
                "projectedNd",
                "contribution",
                "cumsum_contribution",
            ],
        ),
        ("optinist/dimension_reduction/cca", ["projectedNd", "coef"]),
        ("optinist/dimension_reduction/tsne", ["projectedNd"]),
        ("optinist/neural_population_analysis/correlation", ["corr"]),
        (
            "optinist/neural_population_analysis/hierarchical_clustering",
            ["sorted_heatmap", "cluster_mean", "cluster_size", "figure"],
        ),
        (
            "optinist/neural_population_analysis/granger",
            ["Granger_fval_mat_heatmap", "Granger_fval_mat_scatter"],
        ),
        ("optinist/neural_decoding/svm", ["score"]),
        ("optinist/neural_decoding/lda", ["score"]),
        ("optinist/neural_decoding/glm", ["actual_predicted", "params"]),
        ("caiman/caiman_mc", ["mc_images", "meanImg", "refImg"]),
        ("caiman/caiman_cnmf", ["fluorescence", "dff", "iscell", "meanImg"]),
        ("caiman/caiman_cnmfe", ["fluorescence", "dff", "iscell", "meanImg"]),
        ("caiman/cnmf_multisession", ["fluorescence", "dff", "iscell", "meanImg"]),
        (
            "suite2p/suite2p_registration",
            ["ops", "mc_images", "meanImg", "refImg"],
        ),
    ],
)
def test_declared_returns(path, expected_returns):
    algos = _collect_algos(NestDictGetter.get_nest_dict(wrapper_dict, ""), {})

    assert path in algos
    assert [r.name for r in algos[path].returns] == expected_returns


def test_cell_grouping_is_removed():
    algos = _collect_algos(NestDictGetter.get_nest_dict(wrapper_dict, ""), {})
    assert "optinist/basic_neural_analysis/cell_grouping" not in algos


def test_run(client):
    response = client.get("/algolist")
    output = response.json()

    assert response.status_code == 200
    assert isinstance(output, dict)
    assert "caiman" in output
    assert "children" in output["caiman"]
    assert "caiman_mc" in output["caiman"]["children"]

    assert "args" in output["caiman"]["children"]["caiman_mc"]
    assert "path" in output["caiman"]["children"]["caiman_mc"]
    assert "suite2p" in output


def test_NestDictGetter():
    output = NestDictGetter.get_nest_dict(wrapper_dict, "")

    assert isinstance(output, dict)
    assert "caiman" in output
    assert "children" in output["caiman"]
    assert "caiman_mc" in output["caiman"]["children"]

    assert isinstance(output["caiman"]["children"]["caiman_mc"], Algo)


def test_binning_and_split_nodes_expose_their_ports():
    nodes = NestDictGetter.get_nest_dict(wrapper_dict, "")["optinist"]["children"][
        "basic_neural_analysis"
    ]["children"]

    binning, split = nodes["covariate_binning"], nodes["condition_split"]
    assert [(a.name, a.type, a.isNone) for a in binning.args] == [
        ("neural_data", "FluoData", False),
        ("behaviors_data", "BehaviorData", False),
        ("iscell", "IscellData", True),
        ("order_from", "HeatMapData", True),
    ]
    assert [(a.name, a.type) for a in split.args] == [
        ("neural_data", "FluoData"),
        ("behaviors_data", "BehaviorData"),
    ]
    assert [(r.name, r.type) for r in binning.returns] == [
        ("mean", "TimeSeriesData"),
        ("mean_heatmap", "HeatMapData"),
    ]
    assert [(r.name, r.type) for r in split.returns] == [
        ("neural_data", "FluoData"),
        ("behaviors_data", "BehaviorData"),
    ]


def test_eta_takes_a_row_order_heatmap():
    eta = NestDictGetter.get_nest_dict(wrapper_dict, "")["optinist"]["children"][
        "basic_neural_analysis"
    ]["children"]["eta"]
    assert [(a.name, a.type, a.isNone) for a in eta.args] == [
        ("neural_data", "FluoData", False),
        ("behaviors_data", "BehaviorData", False),
        ("iscell", "IscellData", True),
        ("order_from", "HeatMapData", True),
    ]


def test_caiman_dff_is_a_fluo_port():
    caiman = NestDictGetter.get_nest_dict(wrapper_dict, "")["caiman"]["children"]
    for name in ("caiman_cnmf", "caiman_cnmfe", "cnmf_multisession"):
        returns = {r.name: r.type for r in caiman[name].returns}
        assert returns["dff"] == "FluoData", name


def test_clustering_takes_eta_mean():
    nodes = NestDictGetter.get_nest_dict(wrapper_dict, "")["optinist"]["children"]
    eta = nodes["basic_neural_analysis"]["children"]["eta"]
    clustering = nodes["neural_population_analysis"]["children"][
        "hierarchical_clustering"
    ]

    # The GUI connects a handle only to a port of the same type name
    assert [(a.name, a.type, a.isNone) for a in clustering.args] == [
        ("neural_data", "TimeSeriesData", False)
    ]
    assert ("mean", "TimeSeriesData") in [(r.name, r.type) for r in eta.returns]


@pytest.fixture
def no_env_found_yet(monkeypatch):
    monkeypatch.setattr(algolist, "_built_conda_envs", set())
    algolist._conda_env_filepath.cache_clear()
    yield
    algolist._conda_env_filepath.cache_clear()


def _conda_env_flags(output):
    """conda_env_exists of each algorithm that runs in a conda env, by path."""
    algos = {}
    for value in output.values():
        if "children" in value:
            algos.update(_conda_env_flags(value["children"]))
        elif value["conda_name"]:
            algos[value["path"]] = value["conda_env_exists"]
    return algos


def test_an_env_is_checked_until_found_built_and_then_no_more(
    client, monkeypatch, no_env_found_yet
):
    built, checks, searches = [False], [], Counter()
    search = SmkUtils.get_conda_env_filepath.__func__

    def counting_search(cls, conda_name):
        searches[conda_name] += 1
        return search(cls, conda_name)

    def check(conda_name, conda_env_filepath):
        assert os.path.exists(conda_env_filepath)
        checks[-1][conda_name] += 1
        return built[0]

    monkeypatch.setattr(
        SmkUtils, "get_conda_env_filepath", classmethod(counting_search)
    )
    monkeypatch.setattr(
        SmkInternalUtils, "verify_conda_env_exists", staticmethod(check)
    )

    def request_algolist():
        checks.append(Counter())
        return _conda_env_flags(client.get("/algolist").json())

    before = request_algolist()
    built[0] = True  # a run builds every env
    after = [request_algolist(), request_algolist()]

    assert before and not any(before.values())
    assert all(after[0].values()) and after[1] == after[0]
    # found on the first check after the build, then not checked again
    assert set(checks[1].values()) == {1} and checks[2] == {}
    # each env file is searched for once
    assert set(searches.values()) == {1} and searches.keys() == checks[0].keys()


@pytest.mark.asyncio
async def test_health_is_answered_while_algolist_checks_an_env(
    monkeypatch, no_env_found_yet
):
    hold = Hold()

    def held_check(conda_name, conda_env_filepath):
        if not hold.started.is_set():
            hold()
        return False

    monkeypatch.setattr(
        SmkInternalUtils, "verify_conda_env_exists", staticmethod(held_check)
    )

    held, health = await health_while_held(hold, "GET", "/algolist")

    assert health.status_code == 200
    assert held.status_code == 200, held.text
    assert hold.released_by_answer == [True]
