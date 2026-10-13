import pytest

from studio.app.common.routers.algolist import NestDictGetter
from studio.app.common.schemas.algolist import Algo
from studio.app.wrappers import wrapper_dict


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
            "optinist/neural_population_analysis/granger",
            ["Granger_fval_mat_heatmap", "Granger_fval_mat_scatter"],
        ),
        ("optinist/neural_decoding/svm", ["score"]),
        ("optinist/neural_decoding/lda", ["score"]),
        ("optinist/neural_decoding/glm", ["actual_predicted", "params"]),
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
