import contextlib
import gc
import itertools
import operator
import os
from typing import Optional

import imageio
import numpy as np
import tifffile

from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filepath_creater import (
    create_directory,
    join_filepath,
)
from studio.app.common.core.utils.json_writer import JsonWriter
from studio.app.common.core.workflow.workflow import OutputPath, OutputType
from studio.app.common.dataclass.base import BaseData
from studio.app.common.dataclass.utils import create_images_list
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.dir_path import DIRPATH

logger = AppLogger.get_logger()


def _tiff_shape(path: str) -> tuple:
    """
    Shape imageio.volread would return for a TIFF, from its headers.

    The last frame is decoded as well, so a file volread cannot read (truncated,
    compressed with a codec that is not installed, or with a description that
    claims more frames than it holds) raises here too.
    """
    with tifffile.TiffFile(path) as tif:
        series = tif.series[0]
        end = None if series.dataoffset is None else series.dataoffset + series.nbytes
        if end is not None and end > tif.filehandle.size:
            raise ValueError(f"{path} is truncated")
        tif.asarray(key=-1, series=0)
        return tuple(int(n) for n in series.shape)


# frames per decoded read when splitting, bounded in bytes
BATCH_BYTES = 64 << 20

# tifffile.memmap raises these for a file it cannot map: compressed or scattered
# pixels, not a TIFF (TiffFileError is no ValueError before tifffile 2026), or a
# mount that cannot map
CANNOT_MAP = (ValueError, OSError, tifffile.TiffFileError)


def open_frames(path: str):
    """
    The frames imageio.volread reads from a file: memory-mapped when its pixels
    are contiguous, so only what is sliced is read, else read in full.
    """
    try:
        return tifffile.memmap(path, mode="r")
    except CANNOT_MAP as e:
        logger.warning("%s is read in full: %s", os.path.basename(path), e)
        return np.asarray(imageio.volread(path))


def iter_frame_batches(path: str, frames: int, batch_size: int):
    """
    The frames imageio.volread reads from a stack file, in batches along the
    first axis: the whole file as one memory-mapped view when its pixels are
    contiguous, else batch_size pages decoded at a time when each page is one
    frame (compressed stacks), and read in full only otherwise.

    The view uses no memory, and walked frame by frame it reads each page of
    the file once. Assigned into an array of another memory order (CaImAn's
    pixels x time memmap), it would be read once per image column, so it would
    have to fit the page cache: util_get_image_memmap_from_files maps files
    itself and assigns them a block of columns at a time.
    """
    try:
        source = tifffile.memmap(path, mode="r")
    except CANNOT_MAP:
        source = None
    if source is not None:
        yield source
        return
    try:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            if len(series.pages) == frames:  # one page per frame
                for i in range(0, frames, batch_size):
                    pages = range(i, min(i + batch_size, frames))
                    yield tif.asarray(key=pages, series=0).reshape(
                        (len(pages),) + series.shape[1:]
                    )
                return
    except tifffile.TiffFileError:
        pass
    logger.info("%s is read in full", os.path.basename(path))
    yield np.asarray(imageio.volread(path))


def _stack_headers(paths: list) -> Optional[list]:
    """
    (shape, dtype) of series 0 of each file, from its headers, when every file
    is a TIFF stack of 2-D frames; None otherwise.
    """
    headers = []
    try:
        for path in paths:
            with tifffile.TiffFile(path) as tif:
                series = tif.series[0]
                headers.append((tuple(int(n) for n in series.shape), series.dtype))
    except Exception:
        return None
    return headers if all(len(shape) == 3 for shape, _ in headers) else None


def _iter_frames(paths: list, headers: list, dtype, batch_bytes: int):
    """
    The frames of the files one at a time, joined in time and cast to dtype as
    `ImageData.data` concatenates them, read in batches of at most batch_bytes.
    """
    frame_shape = headers[0][0][1:]
    frame_bytes = int(np.prod(frame_shape)) * dtype.itemsize
    batch_size = max(1, batch_bytes // frame_bytes)
    for path, (shape, _) in zip(paths, headers):
        name, count = os.path.basename(path), 0
        for batch in iter_frame_batches(path, shape[0], batch_size):
            count += len(batch)
            if batch.shape[1:] != frame_shape or count > shape[0]:
                raise ValueError(
                    f"{name}: read as frames of {batch.shape[1:]}, "
                    f"but its headers give {shape}"
                )
            for frame in batch:
                yield np.asarray(frame, dtype)
        if count != shape[0]:
            raise ValueError(
                f"{name}: read {count} frames, but its headers give {shape[0]}"
            )


class ImageData(BaseData):
    def __init__(
        self,
        data,
        output_dir=DIRPATH.OUTPUT_DIR,
        file_name="image",
        meta: Optional[PlotMetaData] = None,
    ):
        super().__init__(file_name)

        self.json_path = None
        self.meta = meta

        if data is None:
            self.path = None
        elif isinstance(data, str):
            self.path = data
        elif isinstance(data, list) and isinstance(data[0], str):
            self.path = data
        else:
            _dir = join_filepath([output_dir, "tiff", file_name])
            create_directory(_dir)

            _path = join_filepath([_dir, f"{file_name}.tif"])
            tifffile.imwrite(_path, data)
            self.path = [_path]

            del data
            gc.collect()

    def split_image(
        self, output_dir: str, n_files: int = 2, lengths: Optional[list] = None
    ):
        """
        Split the image along the time axis, either into n_files near-equal
        parts or at the explicit per-part frame counts given in lengths.

        TIFF stacks of 2-D frames are written part by part from batches of
        their frames, byte-identical to writing slices of `data`, so the movie
        is not held as a whole; anything else is read whole, once. A failed
        split leaves no parts behind.
        """
        paths = self.path if isinstance(self.path, list) else [self.path]
        headers = _stack_headers(paths)
        image = self.data if headers is None else None
        frames = self.shape[0] if image is None else image.shape[0]

        if lengths is not None:
            if len(lengths) < 2:
                raise ValueError(f"lengths needs at least 2 entries. Got {lengths}.")
            try:
                lengths = [operator.index(length) for length in lengths]
            except TypeError:
                raise ValueError(f"lengths must be integers. Got {lengths}.") from None
            if min(lengths) <= 0:
                raise ValueError(f"lengths must be positive. Got {lengths}.")
            if sum(lengths) != frames:
                raise ValueError(
                    f"sum of lengths ({sum(lengths)}) must equal "
                    f"total frames ({frames})."
                )
        else:
            try:
                n_files = operator.index(n_files)
            except TypeError:
                raise ValueError(
                    f"n_files must be an integer. Got {n_files}."
                ) from None
            if n_files < 2:
                raise ValueError(f"n_files should be greater than 1. Got {n_files}.")
            if frames < n_files:
                raise ValueError(f"cannot split {frames} frames into {n_files} parts.")
            base, extra = divmod(frames, n_files)
            lengths = [base + 1 if i < extra else base for i in range(n_files)]

        name, ext = os.path.splitext(os.path.basename(paths[0]))

        _dir = join_filepath([output_dir, "image_split", name])
        create_directory(_dir)
        save_paths = [
            join_filepath([_dir, f"{name}_{n}{ext}"]) for n in range(len(lengths))
        ]

        try:
            if image is not None:
                offset = 0
                for _path, length in zip(save_paths, lengths):
                    with tifffile.TiffWriter(_path, bigtiff=True) as tif:
                        tif.write(image[offset : offset + length])
                    offset += length
                return save_paths

            frame_shape = headers[0][0][1:]
            dtype = np.result_type(*(dt.newbyteorder("=") for _, dt in headers))
            movie = _iter_frames(paths, headers, dtype, BATCH_BYTES)
            for _path, length in zip(save_paths, lengths):
                part = itertools.islice(movie, length)
                with tifffile.TiffWriter(_path, bigtiff=True) as tif:
                    # shapes tifffile stores as RGB, which older tifffile
                    # cannot stream
                    if length in (3, 4) or frame_shape[1] in (3, 4):
                        tif.write(np.stack(list(part)))
                    else:
                        tif.write(part, shape=(length, *frame_shape), dtype=dtype)
            return save_paths
        except BaseException:
            # a read error mid-split would leave earlier parts and a partial
            # one; a failed node keeps no pickle that could reference them
            for _path in save_paths:
                with contextlib.suppress(OSError):
                    os.remove(_path)
            raise

    def frame_stream(self):
        """
        (shape, dtype, frames) of `data` without holding it: the frames one at a
        time, read in batches of at most BATCH_BYTES, as split_image reads them.
        None when the files are not TIFF stacks of 2-D frames.
        """
        paths = self.path if isinstance(self.path, list) else [self.path]
        headers = _stack_headers(paths)
        if headers is None:
            return None
        dtype = np.result_type(*(dt.newbyteorder("=") for _, dt in headers))
        shape = (sum(s[0] for s, _ in headers),) + headers[0][0][1:]
        return shape, dtype, _iter_frames(paths, headers, dtype, BATCH_BYTES)

    @property
    def data(self):
        if isinstance(self.path, list):
            return np.concatenate([imageio.volread(p) for p in self.path])
        else:
            return np.array(imageio.volread(self.path))

    @property
    def shape(self) -> tuple:
        """
        Shape of `data`, read from the TIFF headers rather than the pixels.

        `data` reads every frame from disk on each access, so asking it for
        `ndim` or `len` costs a full read of the stack. Falls back to that read
        when the headers cannot be parsed or the files do not stack cleanly, so
        a file `data` cannot read raises as it does there.
        """
        paths = self.path if isinstance(self.path, list) else [self.path]
        try:
            shapes = [_tiff_shape(p) for p in paths]
        except Exception as e:
            logger.debug("ImageData.shape: headers unreadable, reading data (%s)", e)
            return self.data.shape
        if len(shapes) == 1:
            return shapes[0]
        first = shapes[0]
        if len(first) == 0 or any(
            len(s) != len(first) or s[1:] != first[1:] for s in shapes
        ):
            logger.debug("ImageData.shape: files do not stack, reading data")
            return self.data.shape
        return (sum(s[0] for s in shapes),) + first[1:]

    @property
    def ndim(self) -> int:
        return len(self.shape)

    def save_json(self, json_dir):
        if self.ndim < 3:
            self.json_path = join_filepath([json_dir, f"{self.file_name}.json"])
            JsonWriter.write_as_split(self.json_path, create_images_list(self.data))
            JsonWriter.write_plot_meta(json_dir, self.file_name, self.meta)

    @property
    def output_path(self) -> OutputPath:
        shape = self.shape
        if len(shape) >= 3:
            # self.path will be a list if self.data got into else statement on __init__
            if isinstance(self.path, list) and isinstance(self.path[0], str):
                _path = self.path[0]
            else:
                _path = self.path
            # the viewer serves only _path, so count its frames alone
            if isinstance(self.path, list) and len(self.path) > 1:
                shape = ImageData(_path).shape
            return OutputPath(
                path=_path,
                type=OutputType.IMAGE,
                max_index=shape[0],
            )
        else:
            return OutputPath(
                path=self.json_path,
                type=OutputType.IMAGE,
                max_index=1,
            )
