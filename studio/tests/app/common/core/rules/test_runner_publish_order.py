import os

import pytest

from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.core.utils.pickle_handler import PickleReader


def _stub(monkeypatch, name, result):
    monkeypatch.setattr(Runner, name, classmethod(lambda cls, *args: result))


@pytest.fixture
def output(tmp_path, monkeypatch):
    _stub(monkeypatch, "write_pid_file", None)
    _stub(monkeypatch, "_Runner__read_input_info", {})
    _stub(monkeypatch, "_Runner__align_input_info_content_keys", {"nwbfile": {}})
    _stub(monkeypatch, "_Runner__execute_function", {})
    _stub(monkeypatch, "_Runner__save_func_nwb", {"input": {"k": "v"}})
    return str(tmp_path / "default" / "uid" / "eta_node0000" / "eta.pkl")


def _run(output):
    rule = Rule(input=[], return_arg={}, params={}, output=output, type="eta")
    Runner.run(rule, last_output=[output], run_script_path="")


def test_last_node_pickle_is_published_after_whole_nwb(output, monkeypatch):
    """The observer latches hasNWB on the poll that first sees the pickle."""
    pickle_seen_during_whole_nwb = []

    def save_all_nwb(cls, path, all_nwbfile):
        pickle_seen_during_whole_nwb.append(os.path.exists(output))
        all_nwbfile.pop("input")
        open(path, "wb").close()

    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(save_all_nwb))

    _run(output)

    assert pickle_seen_during_whole_nwb == [False]
    assert os.path.exists(os.path.join(os.path.dirname(output), "..", "whole.nwb"))
    assert not os.path.exists(f"{output}.staged")
    # Pickled before save_all_nwb's pop, so downstream reruns still get "input".
    assert PickleReader.read(output) == {"nwbfile": {"input": {"k": "v"}}}


def test_failed_whole_nwb_leaves_error_pickle_and_no_staged_file(output, monkeypatch):
    def save_all_nwb(cls, path, all_nwbfile):
        raise RuntimeError("nwb write failed")

    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(save_all_nwb))

    _run(output)

    assert not os.path.exists(f"{output}.staged")
    assert PickleReader.check_is_error_node_pickle(PickleReader.read(output))
