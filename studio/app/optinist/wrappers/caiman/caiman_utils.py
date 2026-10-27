import logging
import os
import re
import shutil
import signal
import sys
from contextlib import contextmanager

import numpy as np

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


# Null-default params, so the parameter panel edits them as text
TEXT_PARAM_TYPES = {
    "K": int,
    "rf": int,
    "n_pixels_per_process": int,
    "low_rank_background": bool,
    "enclosed_thr": float,
}


def parse_text_params(flat_params: dict) -> dict:
    parsed = dict(flat_params)
    for key, cast in TEXT_PARAM_TYPES.items():
        value = parsed.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip().lower()
        try:
            if text in ("", "none"):
                parsed[key] = None
            elif cast is bool:
                parsed[key] = {"true": True, "false": False}[text]
            else:
                parsed[key] = cast(text)
        except (KeyError, ValueError):
            raise ValueError(
                f"CaImAn param '{key}' must be {cast.__name__} or empty for None, "
                f"got {value!r}"
            ) from None
    # a number field cannot be cleared, so rf 0 means None: the whole field of view
    if type(parsed.get("rf")) is int and parsed["rf"] == 0:
        parsed["rf"] = None
    return parsed


# The CaImAn releases whose save_memmap only adds C_ORDER_OFFSET to a single
# memmap when border_to_0 is 0 (1.12's is the same code as 1.11.4's)
SAVE_MEMMAP_VERIFIED_CAIMAN = ("1.11.", "1.12.")


def needs_save_memmap(filenames: list, border_to_0: int = 0):
    """
    Why caiman_mc needs the file caiman.save_memmap(filenames, order="C",
    border_to_0=...) writes, or None when MotionCorrect's file with
    (x + float32(0.0001)) + float32(0) per value has the same values.
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


# The CaImAn releases whose evaluate_components_CNN is the one
# evaluate_components_cnn copies (identical from 1.11.2 to 1.12.2; 1.13 rewrote
# its model loading)
CNN_CROPS_VERIFIED_CAIMAN = ("1.11.2", "1.11.3", "1.11.4", "1.12.0", "1.12.1", "1.12.2")


def cnn_crops(A, dims, gSig, patch_size: int = 50) -> list:
    """CaImAn's CNN crops, each copied out of its frame (Fortran order, so
    results match CaImAn bit for bit)."""
    import scipy.ndimage

    half_crop = np.minimum(gSig[0] * 4 + 1, patch_size), np.minimum(
        gSig[1] * 4 + 1, patch_size
    )
    dims = np.array(dims)
    coms = [
        scipy.ndimage.center_of_mass(mm.toarray().reshape(dims, order="F"))
        for mm in A.tocsc().T
    ]
    coms = np.maximum(coms, half_crop)
    coms = np.array([np.minimum(cms, dims - half_crop) for cms in coms]).astype(int)
    return [
        mm.toarray()
        .reshape(dims, order="F")[
            com[0] - half_crop[0] : com[0] + half_crop[0],
            com[1] - half_crop[1] : com[1] + half_crop[1],
        ]
        .copy(order="F")
        for mm, com in zip(A.tocsc().T, coms)
    ]


def evaluate_components_cnn(
    A,
    dims,
    gSig,
    model_name: str = None,
    patch_size: int = 50,
    loaded_model=None,
    isGPU: bool = False,
):
    """
    CaImAn's evaluate_components_CNN (the CNN_CROPS_VERIFIED_CAIMAN releases),
    with its crops from cnn_crops: the same predictions, without a dense frame
    held per component. Its prints are logged instead.
    """
    import cv2
    from caiman.paths import caiman_datadir

    caiman_logger = logging.getLogger("caiman")
    if model_name is None:
        model_name = os.path.join(caiman_datadir(), "model", "cnn_model")

    if not isGPU and "CAIMAN_ALLOW_GPU" not in os.environ:
        caiman_logger.info("GPU run not requested, disabling use of GPUs")
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    try:
        os.environ["KERAS_BACKEND"] = "tensorflow"
        from tensorflow.keras.models import model_from_json

        use_keras = True
        caiman_logger.info("Using Keras")
    except ModuleNotFoundError:
        use_keras = False
        caiman_logger.info("Using Tensorflow")

    if loaded_model is None:
        if use_keras:
            if os.path.isfile(os.path.join(caiman_datadir(), model_name + ".json")):
                model_file = os.path.join(caiman_datadir(), model_name + ".json")
            elif os.path.isfile(model_name + ".json"):
                model_file = model_name + ".json"
            else:
                raise FileNotFoundError(
                    f"File for requested model {model_name} not found"
                )
            with open(model_file, "r") as json_file:
                caiman_logger.info(f"USING MODEL (keras API): {model_file}")
                loaded_model_json = json_file.read()

            loaded_model = model_from_json(loaded_model_json)
            loaded_model.load_weights(model_name + ".h5")
        else:
            import caiman.utils.utils

            if os.path.isfile(os.path.join(caiman_datadir(), model_name + ".h5.pb")):
                model_file = os.path.join(caiman_datadir(), model_name + ".h5.pb")
            elif os.path.isfile(model_name + ".h5.pb"):
                model_file = model_name + ".h5.pb"
            else:
                raise FileNotFoundError(
                    f"File for requested model {model_name} not found"
                )
            caiman_logger.info(f"USING MODEL (tensorflow API): {model_file}")
            loaded_model = caiman.utils.utils.load_graph(model_file)

        caiman_logger.debug("Loaded model from disk")

    crop_imgs = cnn_crops(A, dims, gSig, patch_size)
    final_crops = np.array(
        [
            cv2.resize(im / np.linalg.norm(im), (patch_size, patch_size))
            for im in crop_imgs
        ]
    )
    if use_keras:
        predictions = loaded_model.predict(
            final_crops[:, :, :, np.newaxis], batch_size=32, verbose=1
        )
    else:
        import tensorflow as tf

        tf_in = loaded_model.get_tensor_by_name("prefix/conv2d_20_input:0")
        tf_out = loaded_model.get_tensor_by_name("prefix/output_node0:0")
        with tf.Session(graph=loaded_model) as sess:
            predictions = sess.run(
                tf_out, feed_dict={tf_in: final_crops[:, :, :, np.newaxis]}
            )
            sess.close()

    return predictions, final_crops


@contextmanager
def cnn_crops_copied():
    """
    While active, CaImAn's component evaluation classifies with
    evaluate_components_cnn. Under a CaImAn outside CNN_CROPS_VERIFIED_CAIMAN
    it keeps CaImAn's own function, with a warning.
    """
    import caiman
    import caiman.components_evaluation as evaluation
    import caiman.source_extraction.cnmf.estimates as estimates

    if caiman.__version__ not in CNN_CROPS_VERIFIED_CAIMAN:
        logger.warning(
            f"CaImAn {caiman.__version__}: its own CNN evaluation runs, which "
            "holds a dense frame per component (8 B x components x pixels)"
        )
        yield
        return

    modules = (evaluation, estimates)
    originals = [module.evaluate_components_CNN for module in modules]
    for module in modules:
        module.evaluate_components_CNN = evaluate_components_cnn
    try:
        yield
    finally:
        for module, original in zip(modules, originals):
            module.evaluate_components_CNN = original


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
