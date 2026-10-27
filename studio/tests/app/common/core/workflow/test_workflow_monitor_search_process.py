import time
from unittest.mock import Mock, patch

from psutil import NoSuchProcess

from studio.app.common.core.workflow.workflow_result import WorkflowMonitor
from studio.app.common.schemas.workflow import WorkflowPIDFileData

MODULE = "studio.app.common.core.workflow.workflow_result"
GONE_PID = 999999


def _proc(pid, name, cmdline=None, error=None):
    proc = Mock(pid=pid, info={"pid": pid, "name": name})
    proc.as_dict.side_effect = error or (lambda attrs: {"cmdline": cmdline})
    proc.create_time.return_value = time.time()
    return proc


def _process(pid):
    if pid == GONE_PID:
        raise NoSuchProcess(pid)
    return Mock(pid=pid)


def test_a_process_that_exits_mid_scan_is_skipped():
    pid_data = WorkflowPIDFileData(
        last_pid=GONE_PID,
        func_name="f",
        last_script_file="f.py",
        create_time=time.time(),
    )
    exited = _proc(496, "python3.11", error=NoSuchProcess(496))
    conda = _proc(
        497, "conda", ["conda", "env", "create", "-p", "/app/.snakemake/conda/x_"]
    )

    with patch(f"{MODULE}.Runner.read_pid_file", return_value=pid_data), patch(
        f"{MODULE}.Process", side_effect=_process
    ), patch(f"{MODULE}.process_iter", return_value=[exited, conda]):
        found = WorkflowMonitor("1", "uid").search_process()

    assert found.process.pid == conda.pid
