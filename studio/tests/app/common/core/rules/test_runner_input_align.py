from types import SimpleNamespace

import pytest

from studio.app.common.core.rules.runner import Runner

_align = Runner._Runner__align_input_info_content_keys


def test_wired_output_present_is_renamed():
    rule = SimpleNamespace(return_arg={"fluorescence@node1": "neural_data"})
    input_info = {"node1": {"fluorescence": "value", "nwbfile": {"k": 1}}}

    result = _align(input_info, rule)

    assert result["neural_data"] == "value"
    assert result["nwbfile"] == {"k": 1}


def test_wired_output_missing_raises():
    rule = SimpleNamespace(return_arg={"cell_roi@node1": "roi"})
    input_info = {"node1": {"fluorescence": "value"}}

    with pytest.raises(ValueError, match="cell_roi.*node1"):
        _align(input_info, rule)


def test_legacy_key_without_delimiter_missing_raises():
    rule = SimpleNamespace(return_arg={"cell_roi": "roi"})
    input_info = {"node1": {"fluorescence": "value"}}

    with pytest.raises(ValueError, match="cell_roi"):
        _align(input_info, rule)


def test_source_pickle_stray_keys_do_not_shadow_wired_args():
    rule = SimpleNamespace(
        return_arg={
            "iscell@nodeA": "iscell",
            "fluorescence@nodeB": "neural_data",
        }
    )
    input_info = {
        "nodeA": {"iscell": "wired_iscell", "nwbfile": {}},
        "nodeB": {
            "fluorescence": "value",
            "iscell": "stray_iscell",
            "nwbfile": {},
        },
    }

    result = _align(input_info, rule)

    assert result["iscell"] == "wired_iscell"
    assert result["neural_data"] == "value"
