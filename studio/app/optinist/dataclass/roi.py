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
from studio.app.common.dataclass.image import ImageData, open_frames
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
    are read, otherwise read in full once. A reference to several files is
    concatenated from their mapped frames, so the movie is held once. Pickles
    that hold an array (written before references were stored) are returned
    unchanged.
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
    if len(paths) == 1:
        return open_frames(paths[0])
    # each file mapped where it can be, so the movie is held once, concatenated
    return np.concatenate([open_frames(path) for path in paths])


class RoiMasks:
    """Each ROI's pixels, CSR: ROI i is indices[indptr[i]:indptr[i + 1]], flat."""

    def __init__(self, shape, indptr, indices):
        self.shape = tuple(int(n) for n in shape)
        self.indptr = np.asarray(indptr, dtype=np.int64)
        self.indices = np.asarray(indices, dtype=np.int64)

    @classmethod
    def from_pixels(cls, shape, pixels):
        """From each ROI's flat pixel indices, in any order, repeats allowed."""
        rows = [np.unique(np.asarray(p, dtype=np.int64)) for p in pixels]
        return cls._from_sorted(shape, rows)

    @classmethod
    def from_stack(cls, stack):
        """From a per-ROI stack, one layer at a time: a ROI is its non-NaN pixels."""
        rows = [np.flatnonzero(~np.isnan(stack[i])) for i in range(len(stack))]
        return cls._from_sorted(stack.shape[1:], rows)

    @classmethod
    def _from_sorted(cls, shape, rows):
        indptr = np.zeros(len(rows) + 1, np.int64)
        indptr[1:] = np.cumsum([len(row) for row in rows])
        return cls(shape, indptr, np.concatenate(rows) if rows else [])

    def __len__(self):
        return len(self.indptr) - 1

    def pixels(self, i) -> np.ndarray:
        """ROI i's flat pixel indices, ascending."""
        return self.indices[self.indptr[i] : self.indptr[i + 1]]

    def coords(self, i):
        """ROI i's (rows, columns), in the order a boolean mask selects them."""
        return np.unravel_index(self.pixels(i), self.shape)

    def append(self, pixels):
        """Add a ROI of the given flat pixel indices as the last row."""
        row = np.unique(np.asarray(pixels, dtype=np.int64))
        self.indices = np.concatenate((self.indices, row))
        self.indptr = np.append(self.indptr, len(self.indices))

    def union(self, ids) -> np.ndarray:
        """The pixels of any of the ROIs `ids`."""
        rows = [self.pixels(i) for i in ids]
        return np.unique(np.concatenate(rows)) if rows else np.zeros(0, np.int64)

    def select(self, rows) -> "RoiMasks":
        """The ROIs `rows` (indices or a boolean mask), renumbered from 0."""
        rows = np.arange(len(self))[rows]
        return RoiMasks._from_sorted(self.shape, [self.pixels(i) for i in rows])

    def label_image(self, rows=None) -> np.ndarray:
        """Each pixel's highest ROI among `rows` (all when None), else NaN."""
        selected = np.arange(len(self)) if rows is None else np.arange(len(self))[rows]
        image = np.full(self.shape, np.nan)
        flat = image.reshape(-1)
        for i in np.sort(selected):  # the highest ROI writes last
            flat[self.pixels(i)] = i
        return image

    def state(self) -> dict:
        return {"shape": self.shape, "indptr": self.indptr, "indices": self.indices}


def nwb_roi_label_image(label_image: np.ndarray) -> dict:
    """
    The NWB postprocess entry of the all-ROI label image, all_roi_label_img, as
    one (1, H, W) layer. An HDF5 input reads a rank-3 dataset as ImageData, which
    roi_from_hdf5 takes; a 2D one would read as FluoData.
    """
    return {"all_roi_label_img": label_image[np.newaxis]}


class EditRoiData(BaseData):
    def __init__(self, images, roi_masks: RoiMasks):
        if not isinstance(roi_masks, RoiMasks):
            raise TypeError(f"roi_masks is a {type(roi_masks).__name__}, not RoiMasks")
        self.images: ImageData = images
        self.roi_masks = roi_masks
        self.temp_add_roi: Dict[int, RoiPos] = {}
        self.temp_merge_roi: Dict[float, List[int]] = {}
        self.temp_delete_roi: Dict[float, None] = {}
        self.add_roi = []
        self.merge_roi = []
        self.delete_roi = []

    @property
    def roi_masks(self) -> RoiMasks:
        self.convert_legacy_stack()
        return self._roi_masks

    @roi_masks.setter
    def roi_masks(self, value):
        if isinstance(value, RoiMasks):
            self._roi_masks, self._stack = value, None
        else:
            self._roi_masks, self._stack = None, value

    def convert_legacy_stack(self) -> bool:
        """Convert the stack an older pickle held as `im`; True if there was one."""
        if self._roi_masks is not None:
            return False
        self.roi_masks = RoiMasks.from_stack(self._stack)
        return True

    def __getstate__(self):
        # plain arrays, no RoiMasks class, so an earlier release still loads it
        state = dict(self.__dict__)
        roi_masks, stack = state.pop("_roi_masks"), state.pop("_stack")
        if roi_masks is None:
            state["im"] = stack  # never converted: written back as it was read
        else:
            state["roi_masks"] = roi_masks.state()
        return state

    def __setstate__(self, state):
        state = dict(state)
        if "roi_masks" in state:
            self.roi_masks = RoiMasks(**state.pop("roi_masks"))
        else:
            self.roi_masks = state.pop("im")
        self.__dict__.update(state)

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
