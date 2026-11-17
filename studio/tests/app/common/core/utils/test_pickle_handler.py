import os

from studio.app.common.core.utils.pickle_handler import PickleReader, PickleWriter
from studio.app.dir_path import DIRPATH

workspace_id = "default"
unique_id = "pickle_test"

filepath = f"{DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/func2/func2.pkl"


def test_PickleWriter():
    PickleWriter.write(
        filepath,
        "abc",
    )

    assert os.path.exists(filepath)


filepath = f"{DIRPATH.DATA_DIR}/output_test/{workspace_id}/{unique_id}/func1/func1.pkl"


def test_PickleReader():
    data = PickleReader.read(filepath)

    assert data == "abc"


def test_a_failed_dump_leaves_no_tmp_behind(tmp_path, monkeypatch):
    import pickle

    import pytest

    target = str(tmp_path / "node" / "node.pkl")
    PickleWriter.write(target, "old")

    def enospc(obj, f):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(pickle, "dump", enospc)
    with pytest.raises(OSError):
        PickleWriter.write(target, "new")
    # the old pickle goes first (a stale success must not survive a failed
    # write), and the failed write leaves no .tmp
    assert os.listdir(tmp_path / "node") == []
