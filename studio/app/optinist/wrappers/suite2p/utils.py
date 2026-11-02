import contextlib
import os

import numpy as np

from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.core.workflow.workflow_params import read_default_params


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


def tiff_path(output_dir, file_name):
    """The path ImageData writes an array of this file_name to."""
    return join_filepath([output_dir, "tiff", file_name, f"{file_name}.tif"])


@contextlib.contextmanager
def writable_tiff(output_dir, file_name, shape):
    """
    A new int16 TIFF stack at tiff_path, memory-mapped for writing.

    The empty file is laid out as tifffile writes the whole array, so once
    filled it is byte-identical to writing the array, BigTIFF included
    (tifffile.memmap's own create would align the pixels to 4 KiB instead).
    It is built beside the final path and moved there on a clean exit, so a
    failure leaves no partial file and any earlier one untouched.
    """
    import tifffile

    path = tiff_path(output_dir, file_name)
    create_directory(os.path.dirname(path))
    tmp_path = f"{path}.partial"
    try:
        tifffile.imwrite(tmp_path, shape=shape, dtype=np.int16)
        # mapped as its series, which has no frame axis for one frame
        frames = tifffile.memmap(tmp_path, mode="r+").reshape(shape)
        yield frames
        frames.flush()
        del frames
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


class FrameCopies:
    """
    A read-only movie whose slices are writable copies.

    suite2p's registration shifts the bidiphase of the frames it reads in
    place, through a tensor that shares their memory: on a read-only memory
    map that write kills the process with no traceback, and on a writable one
    it changes the file.
    """

    def __init__(self, movie):
        self.movie = movie

    @property
    def shape(self):
        return self.movie.shape

    def __getitem__(self, indices):
        return np.require(self.movie[indices], requirements="W")
