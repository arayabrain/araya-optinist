import os

import numpy as np

from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.core.workflow.workflow_params import read_default_params

# tifffile.imwrite switches to BigTIFF above this many bytes (its `bigsize`)
BIGTIFF_BYTES = 2**32 - 2**25
# frames per read, bounded in bytes
BATCH_BYTES = 64 << 20


def merge_settings(defaults: dict, params: dict) -> dict:
    """Overlay a node's yaml params on suite2p's settings tree, in place."""
    for key, value in (params or {}).items():
        if key not in defaults:
            raise KeyError(f"unknown suite2p setting '{key}'")
        if isinstance(defaults[key], dict):
            if not isinstance(value, dict):
                raise TypeError(f"suite2p setting group '{key}' must be a mapping")
            merge_settings(defaults[key], value)
        elif isinstance(value, dict):
            raise TypeError(f"suite2p setting '{key}' is a value, not a group")
        else:
            defaults[key] = value
    return defaults


def load_settings(defaults: dict, name: str, params: dict) -> dict:
    """The node's shipped yaml over suite2p's tree, then the saved params."""
    return merge_settings(merge_settings(defaults, read_default_params(name)), params)


def pair(value, name: str, kind=int) -> list:
    """Two numbers (y, x), as the parameter form may save any list length."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be two numbers (y, x), got {value!r}")
    return [kind(v) for v in value]


def positive(value, name: str):
    """A setting suite2p divides by, allocates with or thresholds on."""
    if not value > 0:
        raise ValueError(f"{name} must be above 0, got {value!r}")
    return value


def binary_to_tiff(binary, output_dir, file_name, batch_size=None):
    """
    Write an open suite2p BinaryFile to the TIFF path ImageData would use.

    Byte-identical to writing the whole array, with one batch in memory.
    tifffile picks BigTIFF from `data.nbytes`, which a frame iterator does not
    have, so the choice is made here the same way. The file is written beside
    the final path and moved there when complete, so a failed write leaves no
    partial mc_images.tif.
    """
    import tifffile

    tiff_dir = join_filepath([output_dir, "tiff", file_name])
    create_directory(tiff_dir)
    tiff_path = join_filepath([tiff_dir, f"{file_name}.tif"])
    tmp_path = f"{tiff_path}.partial"

    nframes, Ly, Lx = binary.shape
    frame_bytes = Ly * Lx * np.dtype(np.int16).itemsize
    if batch_size is None:
        batch_size = max(1, BATCH_BYTES // frame_bytes)

    def frames():
        for start in range(0, nframes, batch_size):
            yield from np.asarray(binary[start : start + batch_size], dtype=np.int16)

    try:
        # tifffile stores these shapes as RGB, which 2022.3 cannot stream
        if nframes in (3, 4) or Lx in (3, 4):
            tifffile.imwrite(tmp_path, np.stack(list(frames())))
        else:
            tifffile.imwrite(
                tmp_path,
                frames(),
                shape=(nframes, Ly, Lx),
                dtype=np.int16,
                bigtiff=nframes * frame_bytes > BIGTIFF_BYTES,
            )
        os.replace(tmp_path, tiff_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    return [tiff_path]
