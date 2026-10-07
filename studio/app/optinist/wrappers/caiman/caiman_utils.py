import os
import re
import shutil
import signal
import sys
from contextlib import contextmanager

from studio.app.common.core.logger import AppLogger

logger = AppLogger.get_logger()


def distribute_params_to_groups(flat_params: dict, groups: dict) -> dict:
    """
    Convert a flat CaImAn params dict to the pathed {group: {key: value}} form.

    A key is copied into every group that defines it, matching the legacy
    flat-dict behavior of CNMFParams.change_params. "nb" may only be set under
    "init" (CNMFParams.check_consistency propagates it to the other groups).
    Keys unknown to every group are skipped with a warning.
    """
    pathed = {}
    for key, value in flat_params.items():
        if key == "nb":
            target_groups = ["init"]
        else:
            target_groups = [
                name
                for name, group in groups.items()
                if isinstance(group, dict) and key in group
            ]
        if not target_groups:
            logger.warning(f"CaImAn param '{key}' is unknown to CNMFParams; ignored.")
            continue
        for name in target_groups:
            pathed.setdefault(name, {})[key] = value
    return pathed


def _available_cpus() -> int:
    if hasattr(os, "sched_getaffinity"):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 2


@contextmanager
def caiman_cluster(n_processes: int = 1):
    """
    Set up a CaImAn cluster and guarantee its teardown.

    Yields (dview, n_processes). The requested n_processes is clamped to
    [1, cpu_count - 1] to keep one core free for the orchestrating process.
    While a worker pool is live, SIGTERM raises SystemExit so that a workflow
    cancel also stops the pool instead of orphaning its workers.
    """
    from caiman import stop_server
    from caiman.cluster import setup_cluster

    n_processes = max(1, int(n_processes or 1))
    n_processes = min(n_processes, max(_available_cpus() - 1, 1))

    if n_processes > 1:
        _, dview, n_processes = setup_cluster(
            backend="multiprocessing", n_processes=n_processes
        )
    else:
        _, dview, n_processes = setup_cluster(backend="single")
    logger.info(f"CaImAn cluster ready. n_processes: {n_processes}")

    prev_handler = None
    handler_installed = False
    if dview is not None:
        try:
            prev_handler = signal.signal(
                signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum)
            )
            handler_installed = True
        except ValueError:
            pass
    try:
        yield dview, n_processes
    finally:
        if dview is not None:
            if handler_installed:
                try:
                    signal.signal(signal.SIGTERM, prev_handler or signal.SIG_DFL)
                except ValueError:
                    pass
            stop_server(dview=dview)


class CaimanUtils:
    """
    Utility functions for Caiman
    """

    CAIMAN_TEMP_ENV_VAR_NAME = "CAIMAN_TEMP"

    @staticmethod
    def get_caiman_tempdir() -> str:
        import caiman.paths

        try:
            caiman_tempdir_path = caiman.paths.get_tempdir()
        except Exception:
            caiman_tempdir_path = os.path.join(caiman.paths.caiman_datadir(), "temp")

        return caiman_tempdir_path

    @classmethod
    def set_caimam_byid_tempdir(cls, id: str):
        # If "CAIMAN_TEMP" env var is specified, skip
        if cls.CAIMAN_TEMP_ENV_VAR_NAME in os.environ:
            return

        caiman_tempdir_path = os.path.join(cls.get_caiman_tempdir(), id)
        os.makedirs(caiman_tempdir_path, exist_ok=True)
        os.environ[cls.CAIMAN_TEMP_ENV_VAR_NAME] = caiman_tempdir_path

    @classmethod
    def cleanup_caiman_byid_tempdir(cls, id: str):
        caiman_tempdir_path = cls.get_caiman_tempdir()

        if re.search(f"{id}$", caiman_tempdir_path):
            shutil.rmtree(caiman_tempdir_path)

            if cls.CAIMAN_TEMP_ENV_VAR_NAME in os.environ:
                del os.environ[cls.CAIMAN_TEMP_ENV_VAR_NAME]
