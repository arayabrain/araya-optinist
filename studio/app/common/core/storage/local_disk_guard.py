"""Keep the host's local disk from filling.

The app's data lives on the ECS host's root volume next to docker's images, so
a full disk stops the host itself (SSM, cloud-init), not just the run. Runs and
uploads refuse to start when the free space would drop under a reserve, after
first reclaiming leftovers that nothing references. The reserve comes from
OPTINIST_MIN_FREE_DISK_GIB (default 8; 0 disables; standalone default 0).

Imported inside the CaImAn conda env too (require_free_bytes), so the module
level imports only the stdlib, DIRPATH, MODE and the logger; anything that
pulls in the workflow machinery is imported inside the function that needs it.
"""

import contextlib
import errno
import os
import re
import shutil
import time
from typing import Optional, Tuple

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.mode import MODE
from studio.app.const import INPUT_TIFF_CACHE_DIRNAME
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()

MIN_FREE_DISK_ENV = "OPTINIST_MIN_FREE_DISK_GIB"
DEFAULT_MIN_FREE_GIB = 8.0
GIB = 1024**3
LOW_WARNING_INTERVAL_SEC = 600
RECLAIM_BACKOFF_SEC = 600
USER_HINT = (
    "Delete records you no longer need to free space on the analysis server, "
    "or try again later."
)

# Leftovers of a run that nothing reads once the run is over: CaImAn memmaps,
# the temp files of interrupted atomic writes, a partial TIFF
_LEFTOVER_SUFFIXES = (".mmap", ".pkl.tmp", ".yaml.tmp", ".partial")
# files.py `_atomic_replace` (`.<hex>.tmp`) and json_writer (`<name>.<hex>.tmp`)
_ATOMIC_TMP = re.compile(r"\.[0-9a-f]{32}\.tmp$")
_SKIPPED_DIRS = {".locks", ".snakemake"}

# monotonic times of the last low warning and reclaim in this process, None = never
_state = {"low_warned_at": None, "reclaimed_at": None, "reclaimed_bytes": 0}


class LocalDiskFullError(Exception):
    def __init__(self, free_bytes: int, required_bytes: int):
        self.free_bytes = free_bytes
        self.required_bytes = required_bytes
        super().__init__(
            "The analysis server's local disk is nearly full "
            f"({_gb(free_bytes)} GB free, {_gb(required_bytes)} GB needed). "
            "This is the server's working disk, not your storage quota."
        )


def _elapsed(now: float, since: Optional[float]) -> float:
    return float("inf") if since is None else now - since


def _gb(nbytes: int) -> str:
    return f"{nbytes / 1e9:.1f}"


def min_free_bytes() -> int:
    """The reserve in bytes; 0 when the guard is disabled."""
    default = 0.0 if MODE.IS_STANDALONE else DEFAULT_MIN_FREE_GIB
    raw = os.environ.get(MIN_FREE_DISK_ENV)
    if raw is None:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            logger.warning(
                "%s=%r is not a number; using the default %s",
                MIN_FREE_DISK_ENV,
                raw,
                default,
            )
            value = default
    return int(value * GIB) if value > 0 else 0


def get_free_bytes(path: Optional[str] = None) -> Optional[int]:
    """Free bytes on the filesystem holding path (default: the output dir,
    which is the data mount where the data dir itself is not), or None when
    statfs fails."""
    try:
        return shutil.disk_usage(path or DIRPATH.OUTPUT_DIR).free
    except OSError as e:
        logger.warning("LOCAL_DISK_CHECK_FAILED: %s", e)
        return None


def require_free_bytes(nbytes: int, path: str) -> None:
    """Raise OSError(ENOSPC) when writing nbytes under path would leave less
    than the reserve free. No reclaim: this runs inside a workflow rule."""
    reserve = min_free_bytes()
    if reserve <= 0:
        return
    free = get_free_bytes(path)
    if free is None:
        return
    if free - nbytes < reserve:
        raise OSError(
            errno.ENOSPC,
            f"Not enough local disk for this step: it needs {_gb(nbytes)} GB, "
            f"{_gb(free)} GB is free, and {_gb(reserve)} GB must stay free for "
            "the host. Delete records you no longer need, or try a smaller movie.",
        )


def reserve_file(path: str, nbytes: int) -> None:
    """Create path with nbytes allocated up front (posix_fallocate), under one
    lock per container (one app task per host) so two workers cannot both
    pass the reserve check on the same free space; a lock that cannot be
    taken within the timeout is skipped. ENOSPC and EFBIG leave no file;
    where fallocate is unsupported the file stays sparse."""
    from filelock import FileLock, Timeout

    from studio.app.common.core.utils.filelock_handler import FileLockUtils

    lock = FileLock(
        FileLockUtils.get_lockfile_path(
            os.path.join(DIRPATH.OUTPUT_DIR, "reserve_local_disk")
        ),
        timeout=120,
    )
    try:
        lock.acquire()
    except (Timeout, OSError) as e:
        logger.warning("reserve_file: no lock (%s), proceeding", e)
        lock = None
    try:
        require_free_bytes(nbytes, os.path.dirname(path))
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            if hasattr(os, "posix_fallocate") and nbytes:
                try:
                    os.posix_fallocate(fd, 0, nbytes)
                except OSError as e:
                    if e.errno in (errno.ENOSPC, errno.EFBIG):
                        with contextlib.suppress(OSError):
                            os.remove(path)
                        raise
        finally:
            os.close(fd)
    finally:
        if lock is not None:
            lock.release()


def ensure_local_disk_headroom(extra_bytes: int = 0) -> None:
    """Refuse (LocalDiskFullError) unless the reserve plus extra_bytes is free,
    after reclaiming leftovers. Fails open when the disk cannot be measured."""
    reserve = min_free_bytes()
    if reserve <= 0:
        return
    free = get_free_bytes()
    if free is None:
        return
    required = reserve + max(0, int(extra_bytes))
    now = time.monotonic()
    if (
        free < 2 * reserve
        and _elapsed(now, _state["low_warned_at"]) >= LOW_WARNING_INTERVAL_SEC
    ):
        _state["low_warned_at"] = now
        logger.warning(
            "LOCAL_DISK_LOW: %s GB free on %s (reserve %s GB)",
            _gb(free),
            DIRPATH.OUTPUT_DIR,
            _gb(reserve),
        )
    if free >= required:
        return

    shortfall = required - free
    # A reclaim that did not cover the shortfall leaves nothing for the next
    # caller to free (on the incident host the space was docker's); do not
    # walk the data dir again for every request
    if _elapsed(now, _state["reclaimed_at"]) < RECLAIM_BACKOFF_SEC and (
        _state["reclaimed_bytes"] < shortfall
    ):
        logger.error(
            "LOCAL_DISK_FULL: %s GB free, %s GB required (reclaim skipped)",
            _gb(free),
            _gb(required),
        )
        raise LocalDiskFullError(free, required)

    freed = reclaim_local_caches(target_free_bytes=required)
    _state["reclaimed_at"] = time.monotonic()
    _state["reclaimed_bytes"] = freed
    free = get_free_bytes()
    if free is None or free >= required:
        return
    logger.error(
        "LOCAL_DISK_FULL: %s GB free, %s GB required after reclaiming %s GB",
        _gb(free),
        _gb(required),
        _gb(freed),
    )
    raise LocalDiskFullError(free, required)


def caiman_temp_root() -> str:
    """Where caiman_mc puts its per-run temp dirs, as caiman.paths resolves it,
    without importing caiman (the API server has none)."""
    temp = os.environ.get("CAIMAN_TEMP")
    if temp:
        return temp
    data = os.environ.get("CAIMAN_DATA") or os.path.join(
        os.path.expanduser("~"), "caiman_data"
    )
    return os.path.join(data, "temp")


def experiment_is_running(
    workspace_id: str, unique_id: str, max_age_sec: float
) -> bool:
    """True unless the experiment is known to be over: a held sync lock or a
    RUNNING status with a live process means running; RUNNING with no process
    is a crashed run; an unreadable status counts as running while the
    directory is younger than max_age_sec. Any error counts as running."""
    try:
        from studio.app.common.core.experiment.experiment_reader import ExptConfigReader
        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteSyncLockFileUtil,
        )
        from studio.app.common.core.workflow.workflow import NodeRunStatus
        from studio.app.common.core.workflow.workflow_result import WorkflowMonitor

        if RemoteSyncLockFileUtil.check_sync_lock_file(workspace_id, unique_id):
            return True
        try:
            status = ExptConfigReader.read(workspace_id, unique_id).success
        except Exception:
            status = None
        if status == NodeRunStatus.RUNNING.value:
            return WorkflowMonitor(workspace_id, unique_id).search_process() is not None
        if status is None:
            path = os.path.join(DIRPATH.OUTPUT_DIR, workspace_id, unique_id)
            return time.time() - os.path.getmtime(path) < max_age_sec
        return False
    except Exception as e:
        logger.warning("Treating %s/%s as running: %s", workspace_id, unique_id, e)
        return True


def _experiment_ids(workspace_dir: str) -> list:
    try:
        entries = os.listdir(workspace_dir)
    except OSError:
        return []
    return [
        name
        for name in entries
        if name != INPUT_TIFF_CACHE_DIRNAME
        and not name.startswith(".")
        and os.path.isdir(os.path.join(workspace_dir, name))
    ]


def _is_leftover(filename: str) -> bool:
    return filename.endswith(_LEFTOVER_SUFFIXES) or bool(_ATOMIC_TMP.search(filename))


def _stamp(st: os.stat_result) -> tuple:
    return (st.st_ino, st.st_mtime_ns)


def _unchanged(path: str, stamp: tuple) -> bool:
    """False once a rerun has recreated or written the candidate."""
    try:
        return _stamp(os.stat(path)) == stamp
    except OSError:
        return False


def _old_files(root: str, cutoff: float, match) -> list:
    """(size, path, False, stamp) of the files under root older than cutoff
    that match."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIPPED_DIRS]
        for name in filenames:
            if not match(name):
                continue
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime < cutoff:
                found.append((st.st_size, path, False, _stamp(st)))
    return found


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.stat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def _workspace_ids() -> list:
    """From both data roots: an interrupted upload leaves input/<ws> before
    the workspace has any output."""
    ids = set()
    for root in (DIRPATH.OUTPUT_DIR, DIRPATH.INPUT_DIR):
        try:
            ids.update(n for n in os.listdir(root) if not n.startswith("."))
        except OSError:
            pass
    return sorted(ids)


def _reclaim_candidates(max_age_sec: float, exclude: Optional[Tuple[str, str]]) -> list:
    """(size, path, is_dir, stamp) of everything on the allowlist that no
    running experiment can still need."""
    cutoff = time.time() - max_age_sec
    is_mmap = lambda name: name.endswith(".mmap")  # noqa: E731
    candidates = []
    running_uids = set()

    for workspace_id in _workspace_ids():
        workspace_dir = os.path.join(DIRPATH.OUTPUT_DIR, workspace_id)
        workspace_idle = True
        for unique_id in _experiment_ids(workspace_dir):
            if (workspace_id, unique_id) == exclude or experiment_is_running(
                workspace_id, unique_id, max_age_sec
            ):
                workspace_idle = False
                running_uids.add(unique_id)
                continue
            candidates += _old_files(
                os.path.join(workspace_dir, unique_id), cutoff, _is_leftover
            )
        if workspace_idle:
            # Only the memmaps CaImAn writes next to the cached input TIFF: the
            # TIFF itself is referenced by the input node's pickle
            cache_dir = os.path.join(workspace_dir, INPUT_TIFF_CACHE_DIRNAME)
            if os.path.isdir(cache_dir):
                candidates += _old_files(cache_dir, cutoff, is_mmap)
            input_dir = os.path.join(DIRPATH.INPUT_DIR, workspace_id)
            if os.path.isdir(input_dir):
                candidates += _old_files(
                    input_dir,
                    cutoff,
                    lambda name: is_mmap(name) or bool(_ATOMIC_TMP.fullmatch(name)),
                )

    temp_root = caiman_temp_root()
    if os.path.isdir(temp_root):
        for name in os.listdir(temp_root):
            path = os.path.join(temp_root, name)
            unique_id = name.split("_", 1)[0]
            if not os.path.isdir(path) or unique_id in running_uids:
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime >= cutoff:
                continue
            candidates.append((_dir_size(path), path, True, _stamp(st)))
    return candidates


def reclaim_local_caches(
    max_age_hours: float = 1.0,
    target_free_bytes: Optional[int] = None,
    exclude: Optional[Tuple[str, str]] = None,
) -> int:
    """Delete allowlisted leftovers older than max_age_hours, largest first,
    until target_free_bytes is free (or all of them when None). exclude is one
    (workspace_id, unique_id) to leave alone. Returns the bytes removed."""
    from filelock import FileLock, Timeout

    from studio.app.common.core.utils.filelock_handler import FileLockUtils

    lock_path = FileLockUtils.get_lockfile_path(
        os.path.join(DIRPATH.OUTPUT_DIR, "reclaim_local_caches")
    )
    lock = FileLock(lock_path, timeout=5)
    try:
        lock.acquire()
    except Timeout:
        logger.info("reclaim_local_caches: another reclaim is running, skipped")
        return 0
    except OSError as e:
        logger.warning("reclaim_local_caches: no lock (%s), proceeding", e)
        lock = None
    try:
        freed = 0
        candidates = _reclaim_candidates(max_age_hours * 3600, exclude)
        for size, path, is_dir, stamp in sorted(candidates, reverse=True):
            if target_free_bytes is not None:
                free = get_free_bytes()
                if free is not None and free >= target_free_bytes:
                    break
            # A rerun may have recreated it since discovery
            if not _unchanged(path, stamp):
                continue
            try:
                if is_dir:
                    shutil.rmtree(path)
                else:
                    os.remove(path)
            except OSError as e:
                logger.warning("reclaim_local_caches: could not remove %s: %s", path, e)
                continue
            freed += size
            logger.info("reclaim_local_caches: removed %s (%s GB)", path, _gb(size))
        return freed
    finally:
        if lock is not None:
            lock.release()
