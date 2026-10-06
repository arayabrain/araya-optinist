from studio.app.optinist.wrappers.caiman.caiman_utils import distribute_params_to_groups

GROUPS = {
    "init": {"K": 5, "nb": 1, "gSig": [4, 4]},
    "preprocess": {"p": 1, "noise_range": [0.25, 0.5]},
    "temporal": {"p": 1, "noise_range": [0.25, 0.5], "nb": 1},
    "spatial": {"nb": 1},
    "merging": {"merge_thr": 0.8},
}


def test_key_copied_into_every_containing_group():
    pathed = distribute_params_to_groups({"p": 2, "merge_thr": 0.85}, GROUPS)
    assert pathed == {
        "preprocess": {"p": 2},
        "temporal": {"p": 2},
        "merging": {"merge_thr": 0.85},
    }


def test_nb_goes_to_init_only():
    assert distribute_params_to_groups({"nb": 3}, GROUPS) == {"init": {"nb": 3}}


def test_unknown_key_skipped():
    pathed = distribute_params_to_groups({"max_merge_area": None, "K": 7}, GROUPS)
    assert pathed == {"init": {"K": 7}}


def test_none_value_passes_through():
    pathed = distribute_params_to_groups({"noise_range": None}, GROUPS)
    assert pathed == {
        "preprocess": {"noise_range": None},
        "temporal": {"noise_range": None},
    }


def test_group_dicts_not_aliased():
    pathed = distribute_params_to_groups({"K": 7}, GROUPS)
    pathed["init"]["K"] = 99
    assert GROUPS["init"]["K"] == 5


def test_empty_params():
    assert distribute_params_to_groups({}, GROUPS) == {}


def test_non_dict_group_entries_ignored():
    groups = {**GROUPS, "not_a_group": "value"}
    assert distribute_params_to_groups({"K": 7}, groups) == {"init": {"K": 7}}
