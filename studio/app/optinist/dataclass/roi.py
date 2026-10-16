import gc
from typing import Dict, List, Optional

import imageio
import numpy as np
import tifffile

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


class RoiStack(np.memmap):
    """
    A per-ROI mask stack (n_roi x Ly x Lx) kept in a .npy file next to the
    node's outputs, rather than in memory and in the node pickle.

    It behaves as a read-only array. Pickling the whole stack stores only its
    path, so the node pickle, result polling, post-processing and ROI-edit
    requests do not carry n_roi x Ly x Lx x 8 bytes. Anything derived from it
    (a slice, a fancy-indexed subset, a ufunc or reduction result) is a plain
    array as far as pickling goes, and is serialised with its data as before.
    """

    @classmethod
    def create(cls, path: str, shape: tuple, fill=np.nan, dtype=np.float64):
        """A writable stack at `path`, filled with `fill` one layer at a time."""
        out = np.lib.format.open_memmap(path, mode="w+", dtype=dtype, shape=shape)
        for i in range(shape[0]):
            out[i] = fill
        out.flush()
        return out, path

    @classmethod
    def open(cls, path: str):
        obj = np.load(path, mmap_mode="r").view(cls)
        obj._stack_path = path
        obj._whole_file = True
        return obj

    def __array_finalize__(self, obj):
        super().__array_finalize__(obj)
        # Views, subsets and results are not the file: they pickle their data
        self._stack_path = getattr(obj, "_stack_path", None)
        self._whole_file = False

    def __array_wrap__(self, arr, context=None, *args, **kwargs):
        # Ufunc and reduction results are new data, not the file: plain arrays
        arr = super().__array_wrap__(arr, context, *args, **kwargs)
        if isinstance(arr, RoiStack) and not np.shares_memory(arr, self):
            return arr.view(np.ndarray)
        return arr

    def __reduce__(self):
        if getattr(self, "_whole_file", False) and self._stack_path:
            return (RoiStack.open, (self._stack_path,))
        return np.array(self, subok=False).__reduce__()
