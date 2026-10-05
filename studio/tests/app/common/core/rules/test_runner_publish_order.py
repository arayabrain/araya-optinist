import os
import shutil

from studio.app.common.core.rules.runner import Runner
from studio.app.common.core.snakemake.smk import Rule
from studio.app.common.core.utils.pickle_handler import PickleReader
from studio.app.dir_path import DIRPATH

WORKFLOW_DIR = f"{DIRPATH.OUTPUT_DIR}/default/test_publish_order"
OUTPUT = f"{WORKFLOW_DIR}/eta_node0000/eta.pkl"


def _stub(monkeypatch, name, result):
    monkeypatch.setattr(Runner, name, classmethod(lambda cls, *args: result))


def test_last_node_pickle_is_published_after_whole_nwb(monkeypatch):
    """The observer latches hasNWB on the poll that first sees the pickle (#484)."""
    pickle_seen_during_whole_nwb = []

    def save_all_nwb(cls, path, all_nwbfile):
        pickle_seen_during_whole_nwb.append(os.path.exists(OUTPUT))
        all_nwbfile.pop("input")
        open(path, "wb").close()

    _stub(monkeypatch, "write_pid_file", None)
    _stub(monkeypatch, "_Runner__read_input_info", {})
    _stub(monkeypatch, "_Runner__align_input_info_content_keys", {"nwbfile": {}})
    _stub(monkeypatch, "_Runner__execute_function", {})
    _stub(monkeypatch, "_Runner__save_func_nwb", {"input": {"k": "v"}})
    monkeypatch.setattr(Runner, "save_all_nwb", classmethod(save_all_nwb))

    shutil.rmtree(WORKFLOW_DIR, ignore_errors=True)
    rule = Rule(input=[], return_arg={}, params={}, output=OUTPUT, type="eta")
    Runner.run(rule, last_output=[OUTPUT], run_script_path="")

    assert pickle_seen_during_whole_nwb == [False]
    assert os.path.exists(f"{WORKFLOW_DIR}/whole.nwb")
    assert not os.path.exists(f"{OUTPUT}.staged")
    # Pickled before save_all_nwb's pop, so downstream reruns still get "input".
    assert PickleReader.read(OUTPUT) == {"nwbfile": {"input": {"k": "v"}}}
