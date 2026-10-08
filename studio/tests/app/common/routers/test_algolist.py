from studio.app.common.routers.algolist import NestDictGetter
from studio.app.common.schemas.algolist import Algo
from studio.app.wrappers import wrapper_dict


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
