import math

import numpy as np
import scipy.sparse

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData
from studio.app.optinist.dataclass.roi import (
    RoiMasks,
    edit_images,
    nwb_roi_label_image,
    nwb_roi_table,
)
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()

# Bytes of float64 frames LoadData reads at a time outside LCCD's own windows
BATCH_BYTES = 64 << 20

INT_PARAMS = ("filtersize1", "filtersize2", "fsize", "frame_divider", "f0_frames")
NUMBER_PARAMS = ("sigma", "overlap_threshold", "f0_percentile")
STAGES = ("blob_detector", "roi_integration")


def lccd_config(params):
    """The flattened params, plus each stage's own section on top of them, so
    blob_detector and roi_integration keep their own min_area, max_area and
    sparse (flattening alone lets one section's values overwrite the other's).
    """
    flat = {}
    recursive_flatten_params(params, flat)
    sections = {s: {**flat, **(params.get(s) or {})} for s in STAGES}
    return {**flat, **sections}


def roi_columns(roi):
    """Each ROI's flat pixel indices, ascending, and their weights: the nonzero
    entries of each column of LCCD's (pixels, ROIs) matrix, dense or sparse."""
    roi = scipy.sparse.csc_matrix(roi)
    roi.sum_duplicates()
    roi.eliminate_zeros()
    return [
        (roi.indices[start:stop], roi.data[start:stop])
        for start, stop in zip(roi.indptr[:-1], roi.indptr[1:])
    ]


def is_finite_float(value):
    try:
        return math.isfinite(float(value))
    except OverflowError:
        return False


def typed(name, value, kind, errors):
    """value if it is an "integer", or a "number" that is a finite float, else
    None and an error."""
    if kind == "integer":
        ok = isinstance(value, int)
    else:
        ok = isinstance(value, (int, float)) and is_finite_float(value)
    ok = ok and not isinstance(value, bool)
    if not ok:
        expected = "an integer" if kind == "integer" else "a finite number"
        errors.append(f"{name} must be {expected}, got {value!r:.100}")
    return value if ok else None


def validate_params(params, video_shape):
    """Check LCCD parameters against the movie's (rows, columns, frames).

    Only keys present in params are checked. All violations are collected and
    raised together as one ValueError so the user can fix everything at once.
    """
    rows, cols, T = video_shape
    params = params or {}
    config = lccd_config(params)
    errors = []

    v = {k: typed(k, config[k], "integer", errors) for k in INT_PARAMS if k in config}
    for k in NUMBER_PARAMS:
        if k in config:
            v[k] = typed(k, config[k], "number", errors)

    frame_divider = v.get("frame_divider")
    if frame_divider is not None:
        if frame_divider < 1:
            errors.append(f"frame_divider must be >= 1, got {frame_divider}")
        elif frame_divider > T:
            errors.append(
                f"frame_divider ({frame_divider}) is larger than the movie's "
                f"{T} frames, so no chunk is processed: set frame_divider <= {T}"
            )

    filtersize1 = v.get("filtersize1")
    if filtersize1 is not None:
        if filtersize1 < 1:
            errors.append(f"filtersize1 must be >= 1, got {filtersize1}")
        elif frame_divider is not None and 1 <= frame_divider < filtersize1:
            logger.warning(
                "filtersize1 (%d) is larger than frame_divider (%d): the "
                "temporal filters run on chunks of frame_divider frames, which "
                "degrades detection; set filtersize1 <= frame_divider",
                filtersize1,
                frame_divider,
            )

    filtersize2 = v.get("filtersize2")
    if filtersize2 is not None:
        if filtersize2 < 1:
            errors.append(f"filtersize2 must be >= 1, got {filtersize2}")
        elif filtersize1 is not None and filtersize2 >= filtersize1 >= 1:
            errors.append(
                f"filtersize2 ({filtersize2}) must be < filtersize1 ({filtersize1})"
            )

    sigma = v.get("sigma")
    if sigma is not None and sigma <= 0:
        errors.append(f"sigma must be > 0, got {sigma}")

    fsize = v.get("fsize")
    if fsize is not None:
        if not 1 <= fsize <= min(rows, cols):
            errors.append(
                f"fsize ({fsize}) must be between 1 and the frame's shorter "
                f"side ({min(rows, cols)} pixels)"
            )
        elif fsize % 2 == 0:
            logger.warning("fsize (%d) is even; an odd size is recommended", fsize)

    for stage in STAGES:
        prefix = f"{stage}." if stage in params else ""
        area = {
            k: typed(prefix + k, config[stage][k], "number", errors)
            for k in ("min_area", "max_area")
            if k in config[stage]
        }
        min_area, max_area = area.get("min_area"), area.get("max_area")
        if min_area is not None and max_area is not None and min_area >= max_area:
            errors.append(
                f"{prefix}min_area ({min_area}) must be < "
                f"{prefix}max_area ({max_area})"
            )

    overlap_threshold = v.get("overlap_threshold")
    if overlap_threshold is not None and not 0 <= overlap_threshold <= 1:
        errors.append(
            f"overlap_threshold must be between 0 and 1, got {overlap_threshold}"
        )

    f0_frames = v.get("f0_frames")
    if f0_frames is not None:
        if f0_frames < 1:
            errors.append(f"f0_frames must be >= 1, got {f0_frames}")
        elif 2 * f0_frames >= T:
            logger.warning(
                "f0_frames (%d) leaves no frame with a full dff window "
                "[k - f0_frames, k + f0_frames) inside the %d-frame movie: "
                "dff will be entirely NaN; set f0_frames < %d",
                f0_frames,
                T,
                (T + 1) // 2,
            )

    f0_percentile = v.get("f0_percentile")
    if f0_percentile is not None and not 0 <= f0_percentile <= 100:
        errors.append(f"f0_percentile must be between 0 and 100, got {f0_percentile}")

    if errors:
        # dict.fromkeys: flat params put the same area error in both stages
        raise ValueError(
            f"invalid LCCD parameters for a movie of {rows} x {cols} pixels "
            f"(rows x columns) and {T} frames:\n- " + "\n- ".join(dict.fromkeys(errors))
        )


def lccd_detect(
    mc_images: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(fluorescence=FluoData, iscell=IscellData):
    from studio.app.optinist.wrappers.data_utils.vacant_roi import movie_shape

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start lccd_detect: %s", function_id)

    params = params or {}
    validate_params(params, movie_shape(mc_images))

    from studio.app.optinist.wrappers.lccd.lccd_python.lccd import LCCD

    params = lccd_config(params)
    logger.debug("params: %s", params)
    lccd = LCCD(params)
    with LoadData(mc_images) as D:
        assert (
            len(D.shape) == 3
        ), "input array should have dimensions (rows, columns, time)"
        try:
            roi = lccd.apply(D)
            num_cell = roi.shape[1]
        except ValueError as e:
            if "No roi region found" in str(e):
                logger.warning("No ROIs found in LCCD detection")
                num_cell = 0
                roi = np.zeros((D.shape[0] * D.shape[1], 0))  # Empty ROIs
            else:
                raise e
        timeseries = D.roi_means(roi)

    num_frames = D.shape[2]

    # each ROI's pixels, flat in the (X, Y) order roi's rows reshape to
    columns = roi_columns(roi)
    del roi
    roi_masks = RoiMasks.from_pixels(D.shape[:2], (pixels for pixels, _ in columns))

    # Handle zero ROIs case
    if num_cell == 0:
        iscell = np.array([], dtype=int)
        timeseries_dff = np.zeros((0, num_frames))
    else:
        dff_f0_frames = params["f0_frames"]
        dff_f0_percentile = params["f0_percentile"]
        iscell = np.ones(num_cell, dtype=int)

        timeseries_dff = np.ones([num_cell, num_frames]) * np.nan
        for i in range(num_cell):
            for k in range(num_frames):
                if (k - dff_f0_frames >= 0) and (k + dff_f0_frames < num_frames):
                    f0 = np.percentile(
                        timeseries[i, k - dff_f0_frames : k + dff_f0_frames],
                        dff_f0_percentile,
                    )
                    timeseries_dff[i, k] = (timeseries[i, k] - f0) / f0

    # Create ROI list for NWB: (row, column, weight) of each ROI's pixels
    roi_list = []
    for i, (_, weights) in enumerate(columns):
        rows, cols = roi_masks.coords(i)
        roi_list.append({"pixel_mask": np.array([rows, cols, weights]).T})
    all_roi = roi_masks.label_image()

    nwbfile = {}
    nwbfile[NWBDATASET.ROI] = {
        function_id: nwb_roi_table(roi_list, roi_masks.shape, kwargs.get("nwbfile"))
    }
    nwbfile[NWBDATASET.POSTPROCESS] = {function_id: nwb_roi_label_image(all_roi)}

    nwbfile[NWBDATASET.COLUMN] = {
        function_id: {
            "name": "iscell",
            "description": "two columns - iscell & probcell",
            "data": iscell,
        }
    }

    nwbfile[NWBDATASET.FLUORESCENCE] = {
        function_id: {
            "Fluorescence": {
                "table_name": "Fluorescence",
                "region": list(range(len(timeseries))),
                "name": "Fluorescence",
                "data": timeseries,
                "unit": "lumens",
                "rate": nwbfile.get("imaging_plane", {}).get("imaging_rate", 30),
            }
        }
    }

    info = {
        "dff": FluoData(timeseries_dff, file_name="dff"),
        "fluorescence": FluoData(timeseries, file_name="fluorescence"),
        "iscell": IscellData(iscell),
        "all_roi": RoiData(
            all_roi,
            output_dir=output_dir,
            file_name="all_roi",
        ),
        "cell_roi": RoiData(
            roi_masks.label_image(iscell != 0),
            output_dir=output_dir,
            file_name="cell_roi",
        ),
        "non_cell_roi": RoiData(
            np.full(D.shape[:2], np.nan),
            output_dir=output_dir,
            file_name="non_cell_roi",
        ),
        # A reference to the movie when it lives in this experiment; ROI
        # editing opens it lazily
        "edit_roi_data": EditRoiData(
            images=edit_images(mc_images, output_dir),
            roi_masks=roi_masks,
        ),
        "nwbfile": nwbfile,
    }

    return info


class LoadData:
    """
    The movie LCCD detects on: (rows, columns, frames), float64, scaled to [0, 1] by its
    min and max. Frames are read from the first TIFF of mc_images when sliced:
    - LCCD.apply slices `[:, :, start:stop]`, one window at a time
    - roi_means reads BATCH_BYTES of frames at a time
    so the whole movie is never held.
    """

    def __init__(self, mc_images: ImageData):
        from PIL import Image

        self._pile = Image.open(mc_images.path[0])
        try:
            width, height = self._pile.size
            self.shape = (height, width, self._pile.n_frames)
            lows, highs = [], []
            for start, stop in self._batches():
                frames = self._read(range(start, stop))
                lows.append(np.min(frames))
                highs.append(np.max(frames))
            self.minval, self.maxval = np.min(lows), np.max(highs)
        except BaseException:
            self._pile.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._pile.close()

    def __getitem__(self, key) -> np.ndarray:
        rows, cols, frames = key
        window = self._read(range(*frames.indices(self.shape[2])))
        window -= self.minval
        window /= self.maxval - self.minval
        return window[rows, cols]

    def roi_means(self, roi) -> np.ndarray:
        """Each ROI's (column of roi's) mean over its pixels, per frame; roi is
        (pixels, ROIs), dense or sparse."""
        X, Y, T = self.shape
        timeseries = np.zeros([roi.shape[1], T])
        if roi.shape[1] == 0:
            return timeseries
        pixels = [p[weights > 0] for p, weights in roi_columns(roi)]
        for start, stop in self._batches():
            frames = self[:, :, start:stop].reshape(X * Y, stop - start)
            for i, p in enumerate(pixels):
                timeseries[i, start:stop] = np.mean(frames[p, :], axis=0)
        return timeseries

    def _read(self, frames: range) -> np.ndarray:
        window = np.empty((*self.shape[:2], len(frames)))
        for j, i in enumerate(frames):
            self._pile.seek(i)
            window[:, :, j] = np.asarray(self._pile)
        return window

    def _batches(self):
        """
        (start, stop) ranges of about BATCH_BYTES of float64 frames, each at
        least 2 frames wide (unless the movie is 1 frame): a mean over a
        1-frame-wide array sums pixels pairwise, not in order, so it can differ
        in the last bit from the same frame in a wider array.
        """
        X, Y, T = self.shape
        size = max(2, BATCH_BYTES // (X * Y * np.dtype(np.float64).itemsize))
        edges = list(range(0, T, size)) + [T]
        if len(edges) > 2 and edges[-1] - edges[-2] == 1:
            del edges[-2]  # fold a 1-frame tail into the batch before it
        return zip(edges[:-1], edges[1:])
