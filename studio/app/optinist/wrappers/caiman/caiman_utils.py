import contextlib
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


# The CaImAn releases whose save_memmap save_memmap_c_order reproduces (1.12's
# is the same code as 1.11.4's); any other goes through caiman.save_memmap
SAVE_MEMMAP_VERIFIED_CAIMAN = ("1.11.", "1.12.")
# float32 bytes per block of pixel rows; a block and one temporary are in memory
C_ORDER_BLOCK_BYTES = 128 << 20


def _write_block(f, block):
    block.tofile(f)


def save_memmap_c_order_fallback(filenames: list, border_to_0: int = 0):
    """
    Why save_memmap_c_order passes this call to caiman.save_memmap, or None when
    it writes the file itself as (x + float32(0.0001)) + float32(0) per value.
    """
    import caiman

    if not caiman.__version__.startswith(SAVE_MEMMAP_VERIFIED_CAIMAN):
        return f"CaImAn {caiman.__version__} is not 1.11 or 1.12"
    if len(filenames) != 1:
        return f"{len(filenames)} files"
    if border_to_0 > 0:
        return f"border_to_0={border_to_0} (border_nan is not 'copy')"
    if not str(filenames[0]).endswith(".mmap"):
        return "the input is not a memmap"
    return None


def save_memmap_c_order(
    filenames: list,
    base_name: str,
    border_to_0: int = 0,
    block_bytes: int = C_ORDER_BLOCK_BYTES,
) -> str:
    """
    caiman.save_memmap(filenames, base_name=..., order="C", border_to_0=...) for
    the single motion-corrected file caiman_mc passes, without loading the whole
    movie (and three full-size copies of it: the transposed copy and two
    additions) into memory.

    Same file, same bytes: each value is (x + float32(0.0001)) + float32(0), as
    CaImAn 1.11's save_memmap computes it, laid out pixels x time in C order.
    It is written in blocks of whole pixel rows, in file order. Anything else
    (several files, border_to_0 > 0, another CaImAn release) is passed to
    caiman.save_memmap unchanged.
    """
    import numpy as np
    from caiman import save_memmap
    from caiman.mmapping import load_memmap
    from caiman.paths import fn_relocated, generate_fname_tot

    reason = save_memmap_c_order_fallback(filenames, border_to_0)
    if reason:
        logger.warning("caiman.save_memmap converts the movie in memory: %s", reason)
        return save_memmap(
            filenames, base_name=base_name, order="C", border_to_0=border_to_0
        )

    source, dims, T = load_memmap(filenames[0])  # pixels x time, F order
    n_pixels = int(np.prod(dims))
    fname_tot = generate_fname_tot(base_name, dims, "C")
    fname_tot = fn_relocated(os.path.join(os.path.split(filenames[0])[0], fname_tot))
    rows = max(1, min(n_pixels, block_bytes // (4 * T)))
    # Blocks of whole pixel rows are consecutive in the C-order file, so they
    # are written in file order: a full disk raises OSError here, where a
    # memory-mapped output would die of SIGBUS with nothing cleaned up
    try:
        with open(fname_tot, "wb") as f:
            for p in range(0, n_pixels, rows):
                block = np.asarray(source[p : p + rows], dtype=np.float32)
                _write_block(f, block + np.float32(0.0001) + np.float32(0.0))
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(fname_tot)  # no partial memmap left in the temp dir
        raise
    del source

    fname_new = fn_relocated(f"{fname_tot}_frames_{T}.mmap")
    try:
        os.unlink(fname_new)
    except OSError:
        pass
    os.rename(fname_tot, fname_new)
    return fname_new


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
