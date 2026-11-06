"""reclaim_local_caches: only allowlisted leftovers of runs that are over,
largest first, and never the files a record or a rerun still needs."""

import os
import time
import uuid

import pytest

from studio.app.common.core.storage import local_disk_guard as guard

OLD = time.time() - 2 * 3600
HEX = "0123456789abcdef0123456789abcdef"


def _file(path, size=1, mtime=OLD):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def data(tmp_path, monkeypatch):
    """OUTPUT_DIR, INPUT_DIR and the caiman temp root under tmp_path; which
    experiments are running is decided by the `running` set."""
    output = tmp_path / "output"
    input_ = tmp_path / "input"
    temp = tmp_path / "caiman_temp"
    for d in (output, input_, temp):
        d.mkdir()
    monkeypatch.setattr(guard.DIRPATH, "OUTPUT_DIR", str(output))
    monkeypatch.setattr(guard.DIRPATH, "INPUT_DIR", str(input_))
    monkeypatch.setenv("CAIMAN_TEMP", str(temp))
    running = set()
    monkeypatch.setattr(
        guard,
        "experiment_is_running",
        lambda ws, uid, max_age: (ws, uid) in running,
    )
    return {"output": output, "input": input_, "temp": temp, "running": running}


def _finished_experiment(data, ws="1", uid="expA"):
    root = data["output"] / ws / uid
    leftovers = [
        _file(root / "caiman_cnmf" / "image.tif.caiman_cnmf_d1_9.mmap", 300),
        _file(root / "caiman_cnmf" / "caiman_cnmf.pkl.tmp", 200),
        _file(root / "experiment.yaml.tmp", 10),
        _file(root / "tiff" / "mc_images" / "mc_images.tif.partial", 50),
        _file(root / "dff" / f"dff.json.{HEX}.tmp", 20),
    ]
    kept = [
        _file(root / "caiman_cnmf" / "caiman_cnmf.pkl", 1000),
        _file(root / "experiment.yaml"),
        _file(root / "workflow.yaml"),
        _file(root / "pid.json"),
        _file(root / "error.log"),
        _file(root / "whole.nwb"),
        _file(root / "tiff" / "mc_images" / "mc_images.tif"),
        _file(root / "caiman_cnmf" / "young.mmap", 400, mtime=time.time()),
        _file(root / ".locks" / "stale.mmap"),
    ]
    return leftovers, kept


class TestExperimentLeftovers:
    def test_finished_experiment_loses_its_leftovers_only(self, data):
        leftovers, kept = _finished_experiment(data)
        freed = guard.reclaim_local_caches()
        assert freed == 580
        assert [p for p in leftovers if os.path.exists(p)] == []
        assert all(os.path.exists(p) for p in kept)

    def test_running_experiment_is_untouched(self, data):
        leftovers, kept = _finished_experiment(data)
        data["running"].add(("1", "expA"))
        assert guard.reclaim_local_caches() == 0
        assert all(os.path.exists(p) for p in leftovers + kept)

    def test_excluded_experiment_is_untouched(self, data):
        leftovers, _ = _finished_experiment(data)
        assert guard.reclaim_local_caches(exclude=("1", "expA")) == 0
        assert all(os.path.exists(p) for p in leftovers)

    def test_largest_first_until_the_target_is_met(self, data, monkeypatch):
        leftovers, _ = _finished_experiment(data)
        frees = iter([100, 100, 500])  # re-measured before each delete
        monkeypatch.setattr(guard, "get_free_bytes", lambda path=None: next(frees))
        assert guard.reclaim_local_caches(target_free_bytes=400) == 500
        assert not os.path.exists(leftovers[0])  # 300
        assert not os.path.exists(leftovers[1])  # 200
        assert os.path.exists(leftovers[3])  # 50

    def test_another_reclaim_in_progress_skips(self, data, monkeypatch):
        import filelock

        class Busy:
            def __init__(self, *a, **k):
                pass

            def acquire(self):
                raise filelock.Timeout("busy")

        monkeypatch.setattr(filelock, "FileLock", Busy)
        leftovers, _ = _finished_experiment(data)
        assert guard.reclaim_local_caches() == 0
        assert all(os.path.exists(p) for p in leftovers)


class TestWorkspaceCaches:
    def _workspace(self, data, ws="1"):
        cache = data["output"] / ws / "input_tiff" / "abc" / "tiff" / "image"
        tif = _file(cache / "image.tif", 100)
        cache_mmap = _file(cache / "image.tif.caiman_cnmfe_d1_9.mmap", 500)
        input_tif = _file(data["input"] / ws / "data.tif", 100)
        input_mmap = _file(data["input"] / ws / "data.tif.caiman_cnmf_d1.mmap", 300)
        upload_tmp = _file(data["input"] / ws / f".{HEX}.tmp", 200)
        lock = _file(data["input"] / ws / ".locks" / "data.tif.lock")
        return tif, cache_mmap, input_tif, input_mmap, upload_tmp, lock

    def test_idle_workspace_loses_memmaps_and_upload_temps(self, data):
        tif, cache_mmap, input_tif, input_mmap, upload_tmp, lock = self._workspace(data)
        assert guard.reclaim_local_caches() == 1000
        assert not os.path.exists(cache_mmap)
        assert not os.path.exists(input_mmap)
        assert not os.path.exists(upload_tmp)
        assert os.path.exists(tif)  # the input node's pickle points at it
        assert os.path.exists(input_tif)
        assert os.path.exists(lock)

    def test_upload_temp_in_a_workspace_without_output_is_reclaimed(self, data):
        upload_tmp = _file(data["input"] / "7" / f".{HEX}.tmp", 200)
        kept = _file(data["input"] / "7" / "data.tif", 100)
        assert guard.reclaim_local_caches() == 200
        assert not os.path.exists(upload_tmp)
        assert os.path.exists(kept)

    def test_a_candidate_recreated_since_discovery_is_kept(self, data, monkeypatch):
        _, cache_mmap, *_ = self._workspace(data)
        real = guard._reclaim_candidates

        def discover_then_rerun(*args, **kwargs):
            candidates = real(*args, **kwargs)
            os.remove(cache_mmap)
            _file(cache_mmap, 500, mtime=time.time())
            return candidates

        monkeypatch.setattr(guard, "_reclaim_candidates", discover_then_rerun)
        guard.reclaim_local_caches()
        assert os.path.exists(cache_mmap)

    def test_busy_workspace_keeps_them(self, data):
        files = self._workspace(data)
        _file(data["output"] / "1" / "expB" / "experiment.yaml")
        data["running"].add(("1", "expB"))
        assert guard.reclaim_local_caches() == 0
        assert all(os.path.exists(p) for p in files)


class TestCaimanTempDirs:
    def test_orphaned_dirs_go_and_running_ones_stay(self, data):
        temp = data["temp"]
        _file(data["output"] / "1" / "expA" / "experiment.yaml")
        _file(data["output"] / "2" / "expB" / "experiment.yaml")
        data["running"].add(("2", "expB"))
        finished = _file(temp / "expA_caiman_mc" / "a.mmap", 100)
        running = _file(temp / "expB_caiman_mc" / "b.mmap", 100)
        deleted_record = _file(temp / "zzzz_caiman_mc" / "c.mmap", 100)
        young = _file(temp / "expC_caiman_mc" / "d.mmap", 100, mtime=time.time())
        # A dir's mtime stays put while a file inside it is written, so only
        # the running check keeps a long run's memmap
        for d in ("expA_caiman_mc", "expB_caiman_mc", "zzzz_caiman_mc"):
            os.utime(temp / d, (OLD, OLD))
        assert guard.reclaim_local_caches() == 200
        assert not os.path.exists(finished)
        assert not os.path.exists(deleted_record)
        assert os.path.exists(running)
        assert os.path.exists(young)


class TestExperimentIsRunning:
    """The real check, with its collaborators stubbed."""

    @pytest.fixture
    def stubs(self, monkeypatch, tmp_path):
        from studio.app.common.core.experiment import experiment_reader
        from studio.app.common.core.storage import remote_storage_controller
        from studio.app.common.core.workflow import workflow_result

        state = {"locked": False, "status": "success", "process": object()}
        monkeypatch.setattr(guard.DIRPATH, "OUTPUT_DIR", str(tmp_path))
        monkeypatch.setattr(
            remote_storage_controller.RemoteSyncLockFileUtil,
            "check_sync_lock_file",
            classmethod(lambda cls, ws, uid, raise_error=False: state["locked"]),
        )

        def read(cls, ws, uid):
            if state["status"] is None:
                raise AssertionError("Invalid config yaml file")
            return type("Cfg", (), {"success": state["status"]})()

        monkeypatch.setattr(
            experiment_reader.ExptConfigReader, "read", classmethod(read)
        )
        monkeypatch.setattr(
            workflow_result.WorkflowMonitor,
            "search_process",
            lambda self: state["process"],
        )
        return state

    def test_held_sync_lock_means_running(self, stubs):
        stubs["locked"] = True
        assert guard.experiment_is_running("1", "x", 3600)

    def test_finished_status_is_not_running(self, stubs):
        assert not guard.experiment_is_running("1", "x", 3600)
        stubs["status"] = "error"
        assert not guard.experiment_is_running("1", "x", 3600)

    def test_running_status_needs_a_live_process(self, stubs):
        stubs["status"] = "running"
        assert guard.experiment_is_running("1", "x", 3600)
        stubs["process"] = None  # crashed: RUNNING in the yaml forever
        assert not guard.experiment_is_running("1", "x", 3600)

    def test_unreadable_status_is_running_while_young(self, stubs, tmp_path):
        stubs["status"] = None
        exp = tmp_path / "1" / "x"
        exp.mkdir(parents=True)
        assert guard.experiment_is_running("1", "x", 3600)
        os.utime(exp, (OLD, OLD))
        assert not guard.experiment_is_running("1", "x", 3600)

    def test_an_error_counts_as_running(self, stubs, monkeypatch):
        from studio.app.common.core.storage import remote_storage_controller

        def boom(cls, *a, **k):
            raise RuntimeError("lock check failed")

        monkeypatch.setattr(
            remote_storage_controller.RemoteSyncLockFileUtil,
            "check_sync_lock_file",
            classmethod(boom),
        )
        assert guard.experiment_is_running("1", "x", 3600)


def test_input_tiff_cache_name_is_shared():
    from studio.app.const import INPUT_TIFF_CACHE_DIRNAME

    assert INPUT_TIFF_CACHE_DIRNAME == "input_tiff"
    assert uuid.UUID(HEX)  # the atomic temp pattern is a hex uuid
