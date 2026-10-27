import json
import math
import os
import uuid
from contextlib import suppress
from typing import Any, Optional

import numpy as np
import pandas as pd
import tifffile

from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.schemas.outputs import PlotMetaData


class JsonWriter:
    @classmethod
    def _ensure_parent_dir_exists(cls, filepath):
        """Ensure parent directory exists before writing."""
        parent_dir = os.path.dirname(filepath)
        if parent_dir:
            create_directory(parent_dir)

    @classmethod
    def write(cls, filepath, data):
        cls._ensure_parent_dir_exists(filepath)
        json_str = pd.DataFrame(data).to_json(indent=4)
        with open(filepath, "w") as f:
            f.write(json_str)

    @classmethod
    def write_as_split(cls, filepath, data):
        cls._ensure_parent_dir_exists(filepath)
        json_str = pd.DataFrame(data).to_json(indent=4, orient="split")
        with open(filepath, "w") as f:
            f.write(json_str)

    @classmethod
    def write_plot_meta(cls, dir_name, file_name, data: Optional[PlotMetaData]):
        filepath = join_filepath([dir_name, f"{file_name}.plot-meta.json"])
        if data is not None:
            with open(filepath, "w") as f:
                json.dump(data.value_present_dict(), f, indent=4)

    @staticmethod
    def sanitize_for_json(obj: Any) -> Any:
        """
        Recursively sanitize data structure for JSON serialization.
        Converts NaN, Inf, -Inf to None (null in JSON).

        Args:
            obj: Object to sanitize (dict, list, float, etc.)

        Returns:
            Sanitized object safe for JSON serialization
        """
        if isinstance(obj, dict):
            return {k: JsonWriter.sanitize_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [JsonWriter.sanitize_for_json(item) for item in obj]
        elif isinstance(obj, float):
            # Check for NaN, Infinity, -Infinity
            if math.isnan(obj) or math.isinf(obj):
                return None  # Convert to null in JSON
            return obj
        else:
            return obj


# A preview request costs about 75 bytes per pixel end to end for integers up to
# 16 bits and about 93 for floats, which print longer, so a window is bounded by
# pixel count per kind. Sized for the smallest tier's API process.
TIFF_PREVIEW_MAX_PIXELS = 8 * 2**20
# Floats and integers wider than 16 bits
TIFF_PREVIEW_MAX_WIDE_PIXELS = 6 * 2**20
# Bound on decoding a whole series, for layouts with no frame-wise access
TIFF_PREVIEW_MAX_DECODE_BYTES = 256 * 2**20


class TiffPreviewTooLargeError(ValueError):
    """A requested preview would not fit the API process's memory."""

    def __init__(self, message: str, max_frames: int):
        super().__init__(message)
        # The message for the client
        self.detail = message
        # Frames from the window's start that fit; 0 if no window of this file does
        self.max_frames = max_frames


def read_tiff_frames(tiff_filepath, start, stop) -> np.ndarray:
    """
    Frames [start, stop) along the first axis of a TIFF's first series, without
    decoding the rest of the file. A 2D image is a single frame.

    Contiguous uncompressed data is memory-mapped, a whole number of pages per
    frame is read page by page, and any other layout is decoded whole, up to
    TIFF_PREVIEW_MAX_DECODE_BYTES.
    """
    with tifffile.TiffFile(tiff_filepath) as tif:
        series = tif.series[0]
        shape = tuple(int(n) for n in series.shape)
        if len(shape) == 2:
            shape = (1,) + shape
        frame_shape = shape[1:]

        start, stop = max(start, 0), min(stop, shape[0])
        if start >= stop:
            return np.empty((0,) + frame_shape, dtype=series.dtype)

        frame_pixels = math.prod(frame_shape)
        max_frames = _max_preview_pixels(series.dtype) // frame_pixels
        if stop - start > max_frames:
            raise TiffPreviewTooLargeError(
                _too_many_frames_message(start, stop, frame_shape, max_frames),
                max_frames,
            )

        if series.dataoffset is not None:
            stack = np.memmap(
                tiff_filepath,
                dtype=np.dtype(tif.byteorder + series.dtype.char),
                mode="r",
                offset=series.dataoffset,
                shape=shape,
            )
            # the map keeps the file's byte order; series.dtype is native
            return np.array(stack[start:stop], dtype=series.dtype)

        # A frame's pages are consecutive, as the series is their C-order stack
        pages_per_frame, remainder = divmod(len(series), shape[0])
        if (
            remainder == 0
            and pages_per_frame * math.prod(series.keyframe.shape) == frame_pixels
        ):
            pages = range(start * pages_per_frame, stop * pages_per_frame)
            frames = tif.asarray(key=pages, series=0)
            return frames.reshape((stop - start,) + frame_shape)

        if series.nbytes > TIFF_PREVIEW_MAX_DECODE_BYTES:
            raise TiffPreviewTooLargeError(
                "This image cannot be read frame by frame, and at "
                f"{series.nbytes / 2**20:.0f} MiB it is too large to preview.",
                0,
            )
        return tif.asarray(series=0).reshape(shape)[start:stop].copy()


def _max_preview_pixels(dtype) -> int:
    if np.issubdtype(dtype, np.integer) and np.dtype(dtype).itemsize <= 2:
        return TIFF_PREVIEW_MAX_PIXELS
    return TIFF_PREVIEW_MAX_WIDE_PIXELS


def _too_many_frames_message(start, stop, frame_shape, max_frames) -> str:
    size = " x ".join(str(n) for n in frame_shape)
    if max_frames == 0:
        return f"A single {size} frame is too large to preview."
    return (
        f"Frames {start + 1}-{stop} are too many to preview at {size}; "
        f"choose at most {max_frames} frames."
    )


def _write_text_atomic(filepath, text):
    # Readers of filepath never see a partly written file. open() rather than
    # mkstemp, so the file keeps the umask's mode instead of owner-only.
    tmp_path = f"{filepath}.{uuid.uuid4().hex}.tmp"
    try:
        with open(tmp_path, "x") as f:
            f.write(text)
        os.replace(tmp_path, filepath)
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def save_tiff2json(tiff_filepath, save_dirpath, start_index=None, end_index=None):
    # start_index and end_index are 1-based and inclusive; 0 reads as 1
    frames = read_tiff_frames(tiff_filepath, start_index - 1, end_index)
    tiffs = [frame.tolist() for frame in frames]
    del frames

    filename, _ = os.path.splitext(os.path.basename(tiff_filepath))
    create_directory(save_dirpath)

    _write_text_atomic(
        join_filepath(
            [save_dirpath, f"{filename}_{str(start_index)}_{str(end_index)}.json"]
        ),
        # No indent: indentation is most of the text, held twice while built
        pd.DataFrame(tiffs).to_json(orient="split"),
    )
