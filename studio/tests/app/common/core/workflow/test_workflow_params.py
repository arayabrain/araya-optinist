import pytest

from studio.app.common.core.workflow import workflow_params
from studio.app.common.core.workflow.workflow_params import (
    get_typecheck_params,
    read_default_params,
)


def _child(value):
    return {"type": "child", "value": value}


def _to_message(params: dict) -> dict:
    return {
        key: {"type": "parent", "children": _to_message(value)}
        if isinstance(value, dict)
        else _child(value)
        for key, value in params.items()
    }


def test_no_known_params_raises_named_yaml_error():
    with pytest.raises(KeyError) as e:
        get_typecheck_params({"transpose": _child(False)}, "dpca")
    detail = e.value.args[0]
    assert detail.startswith("Workflow yaml error, see FAQ")
    assert "'transpose'" in detail
    assert "dpca" in detail


def test_retired_top_level_key_dropped_from_full_saved_tree():
    saved = _to_message(read_default_params("caiman_mc"))
    saved["border_nan"] = _child("min")
    saved["retired_key"] = _child(False)
    params = get_typecheck_params(saved, "caiman_mc")
    assert params["border_nan"] == "min"
    assert "retired_key" not in params


def test_retired_nested_key_dropped_from_full_saved_tree():
    saved = _to_message(read_default_params("caiman_cnmf"))
    saved["merge_params"]["children"]["retired_key"] = _child(None)
    params = get_typecheck_params(saved, "caiman_cnmf")
    assert params["merge_params"] == read_default_params("caiman_cnmf")["merge_params"]


def test_missing_params_are_not_filled_with_defaults():
    saved = _to_message(read_default_params("caiman_mc"))
    del saved["niter_rig"]
    params = get_typecheck_params(saved, "caiman_mc")
    assert "niter_rig" not in params


def test_v1_flat_tree_with_partial_overlap_still_raises():
    # OptiNiSt v1 suite2p_roi kept `tau` at top level but had no v2 groups
    with pytest.raises(KeyError) as e:
        get_typecheck_params(
            {
                "tau": _child(1.0),
                "soma_crop": _child(False),
                "high_pass": _child(100),
            },
            "suite2p_roi",
        )
    detail = e.value.args[0]
    assert detail.startswith("Workflow yaml error, see FAQ")
    assert "unknown parameters 'high_pass', 'soma_crop' for suite2p_roi" in detail
    assert "'tau'" not in detail


def test_unknown_key_dropped_when_all_groups_present():
    saved = _to_message(read_default_params("suite2p_roi"))
    saved["retired_key"] = _child(False)
    params = get_typecheck_params(saved, "suite2p_roi")
    assert "retired_key" not in params
    assert params["tau"] == read_default_params("suite2p_roi")["tau"]


def test_no_overlap_raises_for_group_less_defaults():
    # snakemake.yaml has only scalars, so the missing-group signature cannot fire
    with pytest.raises(KeyError, match="unknown parameters 'foo' for snakemake"):
        get_typecheck_params({"foo": _child(1)}, "snakemake")


def test_flat_key_that_now_lives_in_a_group_raises():
    saved = _to_message(read_default_params("caiman_mc"))
    saved["use_cuda"] = _child(False)  # today under advanced
    with pytest.raises(KeyError, match="unknown parameters 'use_cuda'"):
        get_typecheck_params(saved, "caiman_mc")


def test_retired_key_dropped_when_a_newer_group_is_missing():
    # A record saved before the yaml gained a group, carrying a retired key
    saved = _to_message(read_default_params("caiman_mc"))
    del saved["advanced"]
    saved["retired_key"] = _child(False)
    params = get_typecheck_params(saved, "caiman_mc")
    assert "retired_key" not in params
    assert "advanced" not in params


def test_retired_key_dropped_when_a_newer_scalar_is_missing():
    saved = _to_message(read_default_params("caiman_mc"))
    del saved["niter_rig"]
    saved["retired_key"] = _child(False)
    params = get_typecheck_params(saved, "caiman_mc")
    assert "retired_key" not in params


def test_missing_group_without_unknown_keys_passes():
    saved = _to_message(read_default_params("caiman_mc"))
    del saved["advanced"]
    params = get_typecheck_params(saved, "caiman_mc")
    assert "advanced" not in params
    assert params["niter_rig"] == read_default_params("caiman_mc")["niter_rig"]


@pytest.mark.parametrize(
    "name,group,key",
    [("caiman_mc", None, "gSig_filt"), ("caiman_cnmf", "data_params", "decay_time")],
)
def test_a_key_saved_with_another_shape_is_dropped(name, group, key):
    saved = _to_message(read_default_params(name))
    target = saved[group]["children"] if group else saved
    target[key] = {"type": "parent", "children": {}}
    params = get_typecheck_params(saved, name)
    assert key not in (params[group] if group else params)


def test_no_yaml_returns_the_saved_params_unchecked():
    saved = {"k": _child(1), "g": {"type": "parent", "children": {"j": _child(2)}}}
    assert get_typecheck_params(saved, "plugin_algo") == {"k": 1, "g": {"j": 2}}


def test_list_rooted_yaml_counts_as_no_yaml(monkeypatch):
    monkeypatch.setattr(workflow_params, "read_default_params", lambda name: [1, 2])
    assert get_typecheck_params({"k": _child(1)}, "odd") == {"k": 1}


def test_flat_key_that_now_lives_two_groups_deep_raises(monkeypatch):
    monkeypatch.setattr(
        workflow_params,
        "read_default_params",
        lambda name: {"a": 1, "g": {"h": {"deep": 2}}},
    )
    with pytest.raises(KeyError, match="unknown parameters 'deep'"):
        get_typecheck_params({"a": _child(1), "deep": _child(3)}, "x")
