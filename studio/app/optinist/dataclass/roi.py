import gc
import hashlib
import os
from typing import Dict, List, Optional

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
from studio.app.common.dataclass.image import ImageData
from studio.app.common.dataclass.utils import create_images_list
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.dir_path import DIRPATH
from studio.app.optinist.schemas.roi import RoiPos, RoiStatus

logger = AppLogger.get_logger()


class RoiData(BaseData):
    def __init__(
        self,
        data,
        output_dir=DIRPATH.OUTPUT_DIR,
        file_name="roi",
        meta: Optional[PlotMetaData] = None,
    ):
        super().__init__(file_name)
        self.meta = meta

        images = create_images_list(data)

        _dir = join_filepath([output_dir, "tiff", file_name])
        create_directory(_dir)
        self.path = join_filepath([_dir, f"{file_name}.tif"])
        tifffile.imwrite(self.path, images)

        del images, data
        gc.collect()

    @property
    def data(self):
        data = np.array(imageio.volread(self.path))
        if data.ndim == 3:
            return data[0]
        elif data.ndim == 2:
            return data

    def save_json(self, json_dir):
        self.json_path = join_filepath([json_dir, f"{self.file_name}.json"])
        JsonWriter.write_as_split(self.json_path, create_images_list(self.data))
        JsonWriter.write_plot_meta(json_dir, self.file_name, self.meta)

    @property
    def output_path(self) -> OutputPath:
        return OutputPath(path=self.json_path, type=OutputType.ROI)


class EditImagesUnavailableError(Exception):
    """The movie ROI edits read new traces from is missing, or has changed
    since the node ran."""


# bytes read from each end of a movie file for its fingerprint, and the
# number and size of the blocks sampled evenly in between
FINGERPRINT_BYTES = 1 << 20
FINGERPRINT_SAMPLES, FINGERPRINT_SAMPLE_BYTES = 32, 64 << 10


def movie_fingerprint(paths: list) -> str:
    """
    The size, first and last MiB, and 32 evenly spaced 64 KiB blocks of each
    file: enough to tell a movie rewritten in place, and unlike the mtime it
    survives experiment copies and restores from remote storage.
    """
    digest = hashlib.sha256()
    for path in paths:
        size = os.path.getsize(path)
        digest.update(str(size).encode())
        with open(path, "rb") as f:
            if size <= 2 * FINGERPRINT_BYTES:
                digest.update(f.read())
                continue
            digest.update(f.read(FINGERPRINT_BYTES))
            middle = size - 2 * FINGERPRINT_BYTES
            for k in range(FINGERPRINT_SAMPLES):
                f.seek(FINGERPRINT_BYTES + middle * k // FINGERPRINT_SAMPLES)
                digest.update(f.read(FINGERPRINT_SAMPLE_BYTES))
            f.seek(size - FINGERPRINT_BYTES)
            digest.update(f.read())
    return digest.hexdigest()


def _experiment_dir(node_dirpath: str) -> str:
    """OUTPUT_DIR/{workspace}/{unique_id}, given a node's output directory."""
    return os.path.dirname(os.path.normpath(os.path.abspath(node_dirpath)))


def edit_reference(images: ImageData, output_dir: str) -> Optional[ImageData]:
    """
    A reference a node's EditRoiData can keep instead of the movie's frames, or
    None when the movie is outside the node's experiment directory,
    OUTPUT_DIR/{workspace}/{unique_id}.

    An input file can be replaced in place by an upload of the same name or
    deleted, and the workspace's input_tiff cache is not synced with the
    experiment, so a reference to either could change or vanish. Files in the
    experiment are synced and restored with it. Their paths are kept relative
    to it, so the reference still holds after the experiment is copied or
    OPTINIST_DIR moves, together with a fingerprint, so a commit refuses a
    movie rewritten since the node ran (an upstream re-run that stopped before
    this node ran again).
    """
    experiment_dir = os.path.realpath(_experiment_dir(output_dir))
    paths = images.path if isinstance(images.path, list) else [images.path]
    relative = []
    for path in paths:
        real = os.path.realpath(path)
        try:
            inside = os.path.commonpath([experiment_dir, real]) == experiment_dir
        except ValueError:  # e.g. on another drive
            inside = False
        if not inside:
            return None
        relative.append(os.path.relpath(real, experiment_dir))
    reference = ImageData(relative)
    reference.fingerprint = movie_fingerprint(paths)
    return reference


def edit_images(images: ImageData, output_dir: str):
    """The movie as a node's EditRoiData keeps it: the reference edit_reference
    gives, else the frames themselves, as before references were stored."""
    reference = edit_reference(images, output_dir)
    return images.data if reference is None else reference


def open_edit_images(images, node_dirpath: str):
    """
    The movie an ROI commit reads new traces from, as an array-like.

    EditRoiData can carry a reference (ImageData) rather than the frames, so the
    node pickle need not hold a copy of the movie. The reference is opened here,
    when a commit needs it, relative to the node's experiment directory:
    memory-mapped when the TIFF allows it, so only the pixels a mask selects
    are read, otherwise read in full once. Pickles that hold an array (written
    before references were stored) are returned unchanged.
    """
    if not isinstance(images, ImageData):
        return images
    experiment_dir = _experiment_dir(node_dirpath)
    paths = images.path if isinstance(images.path, list) else [images.path]
    paths = [os.path.join(experiment_dir, p) for p in paths]  # absolute stay so
    names = ", ".join(os.path.basename(p) for p in paths)
    if not all(os.path.isfile(p) for p in paths):
        raise EditImagesUnavailableError(
            f"The movie ROI edits read new traces from is missing ({names}). "
            "Run the workflow again to add or merge ROIs on this node."
        )
    fingerprint = getattr(images, "fingerprint", None)
    if fingerprint is not None and movie_fingerprint(paths) != fingerprint:
        raise EditImagesUnavailableError(
            f"The movie ROI edits read new traces from ({names}) has changed "
            "since this node ran. Run the workflow again to add or merge ROIs."
        )
    if len(paths) > 1:
        logger.warning("ROI edit reads %d files in full", len(paths))
        return ImageData(paths).data
    try:
        return tifffile.memmap(paths[0], mode="r")
    except (ValueError, OSError) as e:  # compressed, tiled, or a mount that cannot map
        logger.warning("ROI edit reads %s in full: %s", paths[0], e)
        return np.asarray(imageio.volread(paths[0]))


class EditRoiData(BaseData):
    def __init__(self, images, im):
        self.images: ImageData = images
        self.im = im
        self.temp_add_roi: Dict[int, RoiPos] = {}
        self.temp_merge_roi: Dict[float, List[int]] = {}
        self.temp_delete_roi: Dict[float, None] = {}
        self.add_roi = []
        self.merge_roi = []
        self.delete_roi = []

    def save_json(self, json_dir):
        pass

    @property
    def temp_merge_roi_list(self) -> list:
        merge_roi = [(k, *v, -1.0) for k, v in self.temp_merge_roi.items()]
        return [id for ids in merge_roi for id in ids]

    def status(self):
        # Deleting a still pending merge undoes it, so its sources are cells
        # again and must not still read as merged. The entry itself stays, so
        # commit still appends the merged ROI's trace.
        undone = {float(id) for id in self.temp_delete_roi}
        merged = [
            (k, *(() if k in undone else v), -1.0)
            for k, v in self.temp_merge_roi.items()
        ]
        return RoiStatus(
            temp_add_roi=list(self.temp_add_roi.keys()),
            temp_merge_roi=[id for ids in merged for id in ids],
            temp_delete_roi=list(self.temp_delete_roi.keys()),
        )

    def commit(self):
        self.add_roi += self.temp_add_roi.keys()
        self.delete_roi += self.temp_delete_roi.keys()
        self.merge_roi += self.temp_merge_roi_list

        self.temp_add_roi = {}
        self.temp_merge_roi = {}
        self.temp_delete_roi = {}

    def cancel(self):
        self.temp_add_roi = {}
        self.temp_merge_roi = {}
        self.temp_delete_roi = {}
