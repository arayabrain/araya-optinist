import fcntl
import os
import signal
import time
from unittest.mock import Mock, patch

from psutil import STATUS_RUNNING, STATUS_ZOMBIE, NoSuchProcess

from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workflow.workflow_result import WorkflowMonitor
from studio.app.common.schemas.workflow import WorkflowPIDFileData, WorkflowProcessInfo
from studio.app.dir_path import DIRPATH

MODULE = "studio.app.common.core.workflow.workflow_result"
GONE_PID = 999999
CONDA_CMD = ["conda", "env", "create", "-p", "/app/.snakemake/conda/x_"]


def _workdir(unique_id):
    return os.path.realpath(join_filepath([DIRPATH.OUTPUT_DIR, "1", unique_id]))


def _proc(pid, name, cmdline=None, error=None, cwd=None):
    proc = Mock(pid=pid, info={"pid": pid, "name": name})
    proc.as_dict.side_effect = error or (lambda attrs: {"cmdline": cmdline})
    proc.create_time.return_value = time.time()
    proc.cwd.return_value = cwd or _workdir("uid")
    return proc


def _process(pid):
    if pid == GONE_PID:
        raise NoSuchProcess(pid)
    return Mock(pid=pid)


def _search(procs, create_time=None):
    pid_data = WorkflowPIDFileData(
        last_pid=GONE_PID,
        func_name="f",
        last_script_file="f.py",
        create_time=create_time or time.time(),
    )
    with patch(f"{MODULE}.Runner.read_pid_file", return_value=pid_data), patch(
        f"{MODULE}.Process", side_effect=_process
    ), patch(f"{MODULE}.process_iter", return_value=procs):
        return WorkflowMonitor("1", "uid").search_process()


def test_a_process_that_exits_mid_scan_is_skipped():
    exited = _proc(496, "python3.11", error=NoSuchProcess(496))
    conda = _proc(497, "conda", CONDA_CMD)

    assert _search([exited, conda]).process.pid == conda.pid


def test_another_runs_conda_build_is_not_taken():
    other = _proc(498, "conda", CONDA_CMD, cwd=_workdir("other"))
    own = _proc(499, "conda", CONDA_CMD)

    assert _search([other, own]).process.pid == own.pid
    assert _search([other]).process is None


def test_a_run_queued_behind_the_build_lock_outlives_the_startup_grace(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(DIRPATH, "SNAKEMAKE_CONDA_ENV_DIR", str(tmp_path))
    three_hours_ago = time.time() - 3 * 60 * 60

    assert _search([], three_hours_ago) is None
    with open(tmp_path / ".build.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert _search([], three_hours_ago).process is None
    assert _search([], three_hours_ago) is None


def test_wait_counts_zombies_and_exited_processes_as_gone():
    zombie, gone, running = Mock(), Mock(), Mock()
    zombie.status.return_value = STATUS_ZOMBIE
    gone.status.side_effect = NoSuchProcess(3)
    running.status.return_value = STATUS_RUNNING

    assert WorkflowMonitor._wait([zombie, gone]) == []
    with patch(f"{MODULE}.CANCEL_GRACE_SECONDS", 0):
        assert WorkflowMonitor._wait([zombie, gone, running]) == [running]


def test_cancel_kills_lingering_children_before_signalling_the_process(tmp_path):
    sent = []
    pip, conda = Mock(pid=2), Mock(pid=1)
    for proc in (pip, conda):
        proc.as_dict.return_value = {"cmdline": ["x"]}
        proc.send_signal.side_effect = lambda sig, pid=proc.pid: sent.append((pid, sig))
    conda.children.return_value = [pip]
    pid_data = Mock(last_script_file=str(tmp_path / "gone.py"))
    monitor = WorkflowMonitor("1", "uid")

    with patch.object(
        monitor,
        "search_process",
        return_value=WorkflowProcessInfo(process=conda, pid_data=pid_data),
    ), patch.object(WorkflowMonitor, "_wait", side_effect=[[pip], []]) as wait:
        assert monitor.cancel_run()

    conda.children.assert_called_once_with(recursive=True)
    assert wait.call_args_list[0].args == ([pip],)
    assert sent == [
        (1, signal.SIGSTOP),
        (2, signal.SIGTERM),
        (2, signal.SIGKILL),
        (1, signal.SIGTERM),
        (1, signal.SIGCONT),
    ]
