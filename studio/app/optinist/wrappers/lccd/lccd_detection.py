import math

import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import ImageData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import EditRoiData, FluoData, IscellData, RoiData
from studio.app.optinist.dataclass.roi import edit_images
from studio.app.optinist.wrappers.optinist.utils import recursive_flatten_params

logger = AppLogger.get_logger()

# Bytes of float64 frames LoadData reads at a time outside LCCD's own windows
BATCH_BYTES = 64 << 20

INT_PARAMS = (
    "filtersize1",
    "filtersize2",
    "fsize",
    "min_area",
    "max_area",
    "frame_divider",
    "f0_frames",
)
FLOAT_PARAMS = ("sigma", "overlap_threshold", "f0_percentile")
# blob_detector and roi_integration keys collide when params are flattened,
# so a single value (the last one flattened) feeds both stages.
SHARED_SECTION_PARAMS = ("min_area", "max_area", "sparse")


def validate_params(params, video_shape):
    """Check LCCD parameters against the video dimensions (X, Y, T).

    Only keys present in params are checked. All violations are collected and
    raised together as one ValueError so the user can fix everything at once.
    """
    X, Y, T = video_shape
    params = params or {}
    errors = []

    p = {}
    recursive_flatten_params(params, p)

    blob, roi = params.get("blob_detector"), params.get("roi_integration")
    if isinstance(blob, dict) and isinstance(roi, dict):
        for key in SHARED_SECTION_PARAMS:
            if key in blob and key in roi and blob[key] != roi[key]:
                logger.warning(
                    "blob_detector.%s (%r) and roi_integration.%s (%r) "
                    "differ; the pipeline currently shares one value for %s "
                    "and uses %r for both stages",
                    key,
                    blob[key],
                    key,
                    roi[key],
                    key,
                    p[key],
                )

    typed = {}
    for key in INT_PARAMS:
        if key in p:
            if isinstance(p[key], int) and not isinstance(p[key], bool):
                typed[key] = p[key]
            else:
                errors.append(f"{key} must be an integer, got {p[key]!r:.100}")
    for key in FLOAT_PARAMS:
        if key in p:
            if (
                isinstance(p[key], (int, float))
                and not isinstance(p[key], bool)
                and math.isfinite(p[key])
            ):
                typed[key] = p[key]
            else:
                errors.append(f"{key} must be a finite number, got {p[key]!r:.100}")

    frame_divider = typed.get("frame_divider")
    if frame_divider is not None:
        if frame_divider < 1:
            errors.append(f"frame_divider must be >= 1, got {frame_divider}")
        elif T / frame_divider < 2:
            if T < 2:
                errors.append(
                    f"the video loaded by LCCD has only {T} frame(s); "
                    f"LCCD requires at least 2 frames"
                )
            else:
                errors.append(
                    f"frame_divider ({frame_divider}) is too large: the video "
                    f"loaded by LCCD has {T} frames and Time / frame_divider "
                    f"must be >= 2, so set frame_divider <= {T // 2}"
                )
        elif T // frame_divider == 2:
            logger.warning(
                "frame_divider (%d) yields a single chunk for %d frames: "
                "only the first %d frames are processed and ROI integration "
                "never runs",
                frame_divider,
                T,
                frame_divider,
            )

    filtersize1 = typed.get("filtersize1")
    if filtersize1 is not None:
        if filtersize1 < 1:
            errors.append(f"filtersize1 must be >= 1, got {filtersize1}")
        else:
            if filtersize1 > T / 2:
                errors.append(
                    f"filtersize1 ({filtersize1}) must be <= Time / 2: the "
                    f"video loaded by LCCD has {T} frames, so set "
                    f"filtersize1 <= {T // 2}"
                )
            elif (
                frame_divider is not None
                and frame_divider >= 1
                and filtersize1 > frame_divider
            ):
                logger.warning(
                    "filtersize1 (%d) is larger than frame_divider (%d): the "
                    "temporal filters run on chunks of frame_divider frames, "
                    "which degrades detection; set filtersize1 <= "
                    "frame_divider",
                    filtersize1,
                    frame_divider,
                )

    filtersize2 = typed.get("filtersize2")
    if filtersize2 is not None:
        if filtersize2 < 1:
            errors.append(f"filtersize2 must be >= 1, got {filtersize2}")
        elif filtersize1 is not None and filtersize2 >= filtersize1:
            errors.append(
                f"filtersize2 ({filtersize2}) must be < filtersize1 " f"({filtersize1})"
            )

    sigma = typed.get("sigma")
    if sigma is not None and sigma <= 0:
        errors.append(f"sigma must be > 0, got {sigma}")

    fsize = typed.get("fsize")
    if fsize is not None:
        if fsize < 1:
            errors.append(f"fsize must be >= 1, got {fsize}")
        elif fsize % 2 == 0:
            logger.warning("fsize (%d) is even; an odd size is recommended", fsize)

    min_area, max_area = typed.get("min_area"), typed.get("max_area")
    if min_area is not None and max_area is not None and min_area >= max_area:
        errors.append(f"min_area ({min_area}) must be < max_area ({max_area})")
    if max_area is not None and max_area >= X * Y:
        errors.append(
            f"max_area ({max_area}) must be < X * Y "
            f"({X} * {Y} = {X * Y} pixels per frame)"
        )

    overlap_threshold = typed.get("overlap_threshold")
    if overlap_threshold is not None and not 0 < overlap_threshold < 1:
        errors.append(
            f"overlap_threshold must be between 0 and 1 (exclusive), "
            f"got {overlap_threshold}"
        )

    f0_frames = typed.get("f0_frames")
    if f0_frames is not None:
        if f0_frames < 1:
            errors.append(f"f0_frames must be >= 1, got {f0_frames}")
        elif 2 * f0_frames >= T:
            logger.warning(
                "f0_frames (%d) leaves no frame with a full dff window "
                "[k - f0_frames, k + f0_frames) inside the %d-frame video: "
                "dff will be entirely NaN; set f0_frames < %d",
                f0_frames,
                T,
                (T + 1) // 2,
            )

    f0_percentile = typed.get("f0_percentile")
    if f0_percentile is not None and not 0 <= f0_percentile <= 100:
        errors.append(f"f0_percentile must be between 0 and 100, got {f0_percentile}")

    if errors:
        raise ValueError(
            f"invalid LCCD parameters for video of {X} x {Y} pixels x {T} "
            f"frames:\n- " + "\n- ".join(errors)
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

    flattened_params = {}
    recursive_flatten_params(params, flattened_params)
    params = flattened_params

    logger.debug("params: %s", params)
    lccd = LCCD(params)
    with LoadData(mc_images) as D:
        assert (
            len(D.shape) == 3
        ), "input array should have dimensions (width, height, time)"
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

    # Handle zero ROIs case
    if num_cell == 0:
        empty_roi = np.full(D.shape[:2], np.nan)
        im = np.zeros((0, *D.shape[:2]))  # Empty stack of ROIs
        iscell = np.array([], dtype=int)
        timeseries_dff = np.zeros((0, num_frames))
    else:
        dff_f0_frames = params["f0_frames"]
        dff_f0_percentile = params["f0_percentile"]
        iscell = np.ones(num_cell, dtype=int)

        roi_list = []
        for i in range(num_cell):
            roi_list.append((roi[:, i].reshape([D.shape[0], D.shape[1]])) * (i + 1))

        im = np.stack(roi_list)
        im = im.astype(np.float64)
        im[im == 0] = np.nan
        im -= 1

        empty_roi = np.full_like(im[0], np.nan)

        timeseries_dff = np.ones([num_cell, num_frames]) * np.nan
        for i in range(num_cell):
            for k in range(num_frames):
                if (k - dff_f0_frames >= 0) and (k + dff_f0_frames < num_frames):
                    f0 = np.percentile(
                        timeseries[i, k - dff_f0_frames : k + dff_f0_frames],
                        dff_f0_percentile,
                    )
                    timeseries_dff[i, k] = (timeseries[i, k] - f0) / f0

    # Create ROI list for NWB
    roi_list = [{"image_mask": roi[:, i].reshape(D.shape[:2])} for i in range(num_cell)]

    nwbfile = {}
    nwbfile[NWBDATASET.ROI] = {function_id: {"roi_list": roi_list}}
    nwbfile[NWBDATASET.POSTPROCESS] = {function_id: {"all_roi_img": im}}

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
            np.nanmax(im, axis=0) if len(im) > 0 else empty_roi,
            output_dir=output_dir,
            file_name="all_roi",
        ),
        "cell_roi": RoiData(
            np.nanmax(im[iscell != 0], axis=0) if len(im) > 0 else empty_roi,
            output_dir=output_dir,
            file_name="cell_roi",
        ),
        "non_cell_roi": RoiData(
            empty_roi, output_dir=output_dir, file_name="non_cell_roi"
        ),
        # A reference to the movie when it lives in this experiment; ROI
        # editing opens it lazily
        "edit_roi_data": EditRoiData(images=edit_images(mc_images, output_dir), im=im),
        "nwbfile": nwbfile,
    }

    return info


class LoadData:
    """
    The movie LCCD detects on: (X, Y, frames), float64, scaled to [0, 1] by its
    min and max. Frames are read from the first TIFF of mc_images when sliced:
    - LCCD.apply slices `[:, :, start:stop]`, one window at a time
    - roi_means reads BATCH_BYTES of frames at a time
    so the whole movie is never held.
    """

    def __init__(self, mc_images: ImageData):
        from PIL import Image

        self._pile = Image.open(mc_images.path[0])
        try:
            self.shape = (*self._pile.size, self._pile.n_frames)
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

    def roi_means(self, roi: np.ndarray) -> np.ndarray:
        """Each ROI's (column of roi's) mean over its pixels, per frame."""
        X, Y, T = self.shape
        timeseries = np.zeros([roi.shape[1], T])
        if roi.shape[1] == 0:
            return timeseries
        pixels = [np.flatnonzero(roi[:, i] > 0) for i in range(roi.shape[1])]
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
