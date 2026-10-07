import json
import os
import signal
import sys
import types
from pathlib import Path

import pytest

from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.optinist.wrappers.caiman import caiman_utils
from studio.app.optinist.wrappers.caiman.caiman_utils import (
    caiman_cluster,
    distribute_params_to_groups,
)
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

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


# Keys per CNMFParams group of the pinned CaImAn, recorded from the conda env with
# {g: sorted(v) for g, v in vars(CNMFParams()).items() if isinstance(v, dict)}
REAL_GROUPS = {
    group: dict.fromkeys(keys)
    for group, keys in json.loads(
        (Path(__file__).parent / "caiman_cnmfparams_groups.json").read_text()
    )["groups"].items()
}
PARAMS_DIR = Path(caiman_utils.__file__).parent / "params"
WRAPPER_KEYS = {
    "caiman_cnmf.yaml": {"Ain", "do_refit", "roi_thr", "use_online", "n_processes"},
    "caiman_cnmfe.yaml": {"Ain", "do_refit", "roi_thr", "use_online", "n_processes"},
    "cnmf_multisession.yaml": {
        "Ain",
        "roi_thr",
        "n_processes",
        "session_lengths",
        "n_reg_files",
        "reg_file_rate",
        "align_flag",
        "max_thr",
        "use_opt_flow",
        "thresh_cost",
        "max_dist",
        "enclosed_thr",
    },
    "caiman_mc.yaml": set(),
}


@pytest.mark.parametrize("yaml_name", sorted(WRAPPER_KEYS))
def test_every_yaml_param_is_a_real_cnmfparams_key(yaml_name):
    """A key the pinned CaImAn does not know is dropped at run time, silently
    for the user. The fixture is the real group map, so a CaImAn bump that
    renames or removes a key fails here instead of in a workflow log."""
    flat = {}
    recursive_flatten_params(ConfigReader.read(PARAMS_DIR / yaml_name), flat)
    for key in WRAPPER_KEYS[yaml_name]:  # popped by the wrapper before distributing
        flat.pop(key, None)

    pathed = distribute_params_to_groups(flat, REAL_GROUPS)

    routed = {key for group in pathed.values() for key in group}
    assert routed == flat.keys()
    for group, keys in pathed.items():
        assert keys.keys() <= set(REAL_GROUPS[group]), group


def test_a_key_named_like_a_group_is_routed_under_its_group():
    # ring_CNN is both a CNMFParams group and a key of the online group;
    # change_params treats a toplevel ring_CNN as the group only when it is a dict
    assert distribute_params_to_groups({"ring_CNN": False}, REAL_GROUPS) == {
        "online": {"ring_CNN": False}
    }


def test_nb_is_shared_by_three_real_groups_but_set_once():
    assert {g for g, keys in REAL_GROUPS.items() if "nb" in keys} == {
        "init",
        "spatial",
        "temporal",
    }
    assert distribute_params_to_groups({"nb": 2}, REAL_GROUPS) == {"init": {"nb": 2}}


class FakePool:
    def __init__(self):
        self.stopped = False


@pytest.fixture
def fake_caiman(monkeypatch):
    """Stub the caiman package: setup_cluster hands out a pool above one
    process, stop_server records the teardown."""
    calls = types.SimpleNamespace(setup=[], stopped=[])

    def setup_cluster(backend, n_processes=None):
        calls.setup.append((backend, n_processes))
        dview = FakePool() if backend == "multiprocessing" else None
        return None, dview, n_processes or 1

    caiman = types.ModuleType("caiman")
    caiman.stop_server = lambda dview: calls.stopped.append(dview)
    cluster = types.ModuleType("caiman.cluster")
    cluster.setup_cluster = setup_cluster
    caiman.cluster = cluster
    monkeypatch.setitem(sys.modules, "caiman", caiman)
    monkeypatch.setitem(sys.modules, "caiman.cluster", cluster)
    monkeypatch.setattr(caiman_utils, "_available_cpus", lambda: 8)
    return calls


@pytest.mark.parametrize(
    "requested,expected",
    [(None, 1), (0, 1), (-3, 1), ("3", 3), (3.0, 3), (4, 4), (99, 7)],
)
def test_cluster_clamps_the_request_to_one_core_short_of_the_host(
    fake_caiman, requested, expected
):
    with caiman_cluster(requested) as (_, n_processes):
        assert n_processes == expected


def test_single_process_takes_no_pool_and_leaves_the_signal_handler(fake_caiman):
    before = signal.getsignal(signal.SIGTERM)
    with caiman_cluster(1) as (dview, n_processes):
        assert (dview, n_processes) == (None, 1)
        assert signal.getsignal(signal.SIGTERM) is before
    assert fake_caiman.setup == [("single", None)]
    assert fake_caiman.stopped == []


def test_a_pool_installs_a_sigterm_handler_only_while_it_is_live(fake_caiman):
    before = signal.getsignal(signal.SIGTERM)
    with caiman_cluster(4) as (dview, _):
        assert isinstance(dview, FakePool)
        assert signal.getsignal(signal.SIGTERM) is not before
    assert signal.getsignal(signal.SIGTERM) is before
    assert fake_caiman.stopped == [dview]


def test_sigterm_during_a_pooled_fit_exits_and_stops_the_pool(fake_caiman):
    with pytest.raises(SystemExit) as exc, caiman_cluster(4) as (dview, _):
        os.kill(os.getpid(), signal.SIGTERM)
    assert exc.value.code == 128 + signal.SIGTERM
    assert fake_caiman.stopped == [dview]


def test_a_failed_fit_still_stops_the_pool(fake_caiman):
    with pytest.raises(RuntimeError), caiman_cluster(2) as (dview, _):
        raise RuntimeError("fit failed")
    assert fake_caiman.stopped == [dview]
