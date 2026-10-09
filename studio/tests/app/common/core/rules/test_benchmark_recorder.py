import json
import sys
from types import SimpleNamespace

import pytest

from studio.app.common.core.rules.benchmark_recorder import (
    ENV_FLAG,
    OUTPUT_DIRNAME,
    OUTPUT_FILENAME,
    BenchmarkRecorder,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="reads /proc"
)


def make_smk(tmp_path, create_output=True):
    node_dir = tmp_path / "workflow" / "node_1"
    node_dir.mkdir(parents=True)
    output = node_dir / "algo.pkl"
    if create_output:
        output.write_bytes(b"x")
    return SimpleNamespace(
        rule="node_1",
        output=[str(output)],
        params=SimpleNamespace(name={"type": "algo"}),
    )


def read_records(tmp_path):
    path = tmp_path / "workflow" / OUTPUT_DIRNAME / OUTPUT_FILENAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture(autouse=True)
def reset_marks():
    BenchmarkRecorder._marks = {}


def test_disabled_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_FLAG, raising=False)
    smk = make_smk(tmp_path)

    with BenchmarkRecorder.record(smk):
        BenchmarkRecorder.mark("imports_done")

    assert read_records(tmp_path) == []
    assert BenchmarkRecorder._marks == {}


def test_enabled_writes_one_record(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    smk = make_smk(tmp_path)

    with BenchmarkRecorder.record(smk):
        BenchmarkRecorder.mark("imports_done")

    (record,) = read_records(tmp_path)
    assert record["rule"] == "node_1"
    assert record["node_id"] == "node_1"
    assert record["status"] == "ok"
    assert record["output_exists"] is True
    assert record["peak_rss_mb"] > 0
    assert record["write_bytes"] is not None
    assert (
        record["t_proc_start"]
        <= record["t_main_start"]
        <= record["t_imports_done"]
        <= record["t_end"]
    )


def test_exception_is_recorded_and_reraised(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    smk = make_smk(tmp_path, create_output=False)

    with pytest.raises(RuntimeError):
        with BenchmarkRecorder.record(smk):
            raise RuntimeError("boom")

    (record,) = read_records(tmp_path)
    assert record["status"] == "error"
    assert "boom" in record["error"]
    assert record["output_exists"] is False


def test_recording_failure_does_not_affect_rule(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_FLAG, "1")
    broken_smk = SimpleNamespace(rule="r", output=[], params=None)

    with BenchmarkRecorder.record(broken_smk):
        pass  # no exception escapes although the record cannot be written
