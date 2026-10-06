import pytest

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
    assert "'soma_crop'" in detail
    assert "'tau'" not in detail


def test_unknown_key_dropped_when_all_groups_present():
    saved = _to_message(read_default_params("suite2p_roi"))
    saved["retired_key"] = _child(False)
    params = get_typecheck_params(saved, "suite2p_roi")
    assert "retired_key" not in params
    assert params["tau"] == read_default_params("suite2p_roi")["tau"]
