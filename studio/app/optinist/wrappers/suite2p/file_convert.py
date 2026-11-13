import os

import numpy as np
import tifffile

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.dataclass import ImageData
from studio.app.optinist.dataclass import Suite2pData
from studio.app.optinist.dataclass.roi import movie_fingerprint

logger = AppLogger.get_logger()


def suite2p_file_convert(
    image: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(ops=Suite2pData):
    from suite2p import default_db, default_settings, io
    from suite2p.io.tiff import open_tiff, use_sktiff_reader

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start suite2p_file_convert: %s", function_id)

    # only the two keys the conversion reads; the reader rule sees them as
    # tiff_to_binary will
    params = params or {}
    db = {
        **default_db(),
        **{k: params[k] for k in ("force_sktiff", "batch_size") if k in params},
    }
    file_list, read_in_place = tiff_list_for_binary(
        image.path, output_dir, db, open_tiff, use_sktiff_reader
    )

    logger.debug(file_list)
    db = {
        **db,
        "data_path": [output_dir],
        "file_list": file_list,
        "save_path0": output_dir,
        "save_folder": "suite2p",
    }
    db["file_list"], db["first_files"] = io.get_file_list(db)
    dbs = io.init_dbs(db)

    # write data.bin, db.npy and settings.npy under save_path0/suite2p/plane0
    try:
        with open(dbs[0]["reg_file"], "wb") as reg_file:
            ops = io.tiff_to_binary(dbs, default_settings(), [reg_file], None)[0]
    except OSError:
        # an input deleted mid-read surfaces as tifffile's FileNotFoundError
        for path, version in read_in_place.items():
            check_unchanged(path, version)
        raise
    for path, version in read_in_place.items():
        check_unchanged(path, version)

    info = {
        "meanImg": ImageData(
            ops["meanImg"], output_dir=output_dir, file_name="meanImg"
        ),
        "ops": Suite2pData(ops, file_name="ops"),
    }

    return info


# tifffile.imwrite switches to BigTIFF above this many bytes (its `bigsize`)
BIGTIFF_BYTES = 2**32 - 2**25
# frames per read when normalising a float32 input, bounded in bytes
BATCH_BYTES = 64 << 20


class InputChangedError(RuntimeError):
    pass


def input_version(path):
    """
    What tells an input rewritten in place (a same-name upload) mid-read: its
    size and sampled content. Not the mtime: the HDF5 / MAT input_tiff cache is
    replaced with identical bytes when two runs fill it at once.
    """
    return os.path.getsize(path), movie_fingerprint([path])


def check_unchanged(path, version):
    if not os.path.exists(path):
        raise InputChangedError(
            f"{path} was deleted while suite2p_file_convert read it. "
            "Run the workflow again."
        )
    if input_version(path) != version:
        raise InputChangedError(
            f"{path} was rewritten while suite2p_file_convert read it. "
            "Run the workflow again."
        )


def tiff_list_for_binary(paths, output_dir, ops, open_tiff, use_sktiff_reader):
    """
    Name each input file for tiff_to_binary, copying only what it cannot read as is.

    get_file_list joins each name to data_path[0] (output_dir); an absolute path
    replaces it, so an input read as is is listed by its own path. Per file:
    - integer pixels, plain layout, the reader tiff_to_binary will use: as is;
    - float32, plain layout: an int16 copy normalised to the file's own min / max,
      written in batches;
    - anything else: the whole-file copy (normalised if float32), as before.
    tiff_to_binary then sees the same frames either way, so data.bin and meanImg
    do not change. `ops` is default_db() with the node's params, the db
    tiff_to_binary reads them from. `open_tiff` and `use_sktiff_reader` are
    suite2p.io.tiff's, passed in because suite2p is imported lazily.

    Returns the list and, for the files read as is, the version each had before
    the read: tiff_to_binary reads them for the whole conversion, so the caller
    checks them after it. A copied file is checked once its copy is written.
    """
    force_sktiff = bool(ops.get("force_sktiff"))
    batch_size = ops.get("batch_size")

    tiff_list = []
    read_in_place = {}
    # the reader tiff_to_binary picks once, from the first listed file
    binary_sktiff = None
    for path in paths:
        version = input_version(path)
        use_sktiff = force_sktiff or use_sktiff_reader(path, batch_size=batch_size)
        layout = plain_layout(path)
        if (
            layout is not None
            and np.issubdtype(layout[1], np.integer)
            and binary_sktiff in (None, use_sktiff)
        ):
            name = os.path.abspath(path)
            listed_sktiff = use_sktiff
            treatment = "as is"
            read_in_place[name] = version
        else:
            name = os.path.basename(path)
            copy_path = join_filepath([output_dir, name])
            if layout is not None and can_normalise_in_batches(*layout):
                write_normalised_copy(path, copy_path, layout[0], use_sktiff, open_tiff)
                treatment = "int16 copy, in batches"
            else:
                write_whole_copy(path, copy_path, use_sktiff, open_tiff)
                treatment = "whole-file copy"
            try:
                check_unchanged(path, version)
            except InputChangedError:
                os.remove(copy_path)
                raise
            listed_sktiff = None  # tiff_to_binary decides it on the copy
        logger.info("suite2p_file_convert: %s (%s)", path, treatment)

        if binary_sktiff is None:
            if listed_sktiff is None:
                listed_sktiff = force_sktiff or use_sktiff_reader(
                    copy_path, batch_size=batch_size
                )
            binary_sktiff = listed_sktiff
        tiff_list.append(name)

    return tiff_list, read_in_place


def plain_layout(path):
    """
    (shape, dtype) of a TIFF whose pages are the frames of its whole-file read,
    else None.

    tiff_to_binary reads by page; the whole-file read returns series 0. They
    agree for one series of (T, Y, X) or (Y, X) grey pages, one page per frame,
    stored in frame order (OME's TiffData can map frames to pages out of order).
    """
    try:
        with tifffile.TiffFile(path) as tif:
            if len(tif.series) != 1:
                return None
            series = tif.series[0]
            shape = tuple(int(n) for n in series.shape)
            frames = shape[0] if len(shape) == 3 else 1
            page = tif.pages[0]
            if (
                len(shape) not in (2, 3)
                or len(tif.pages) != frames
                or page.samplesperpixel != 1
                or tuple(page.shape) != shape[-2:]
                or [getattr(p, "index", None) for p in series.pages]
                != list(range(frames))
            ):
                return None
            return shape, np.dtype(series.dtype)
    except Exception as e:
        logger.debug("suite2p_file_convert: layout unreadable, %s (%s)", path, e)
        return None


def can_normalise_in_batches(shape, dtype):
    # tifffile stores 3- / 4-frame and 3- / 4-column stacks as RGB, and the
    # whole-file copy keeps that result (tifffile 2022.3 could not stream them);
    # those and single frames are copied whole
    return (
        dtype == np.float32
        and len(shape) == 3
        and shape[0] not in (3, 4)
        and shape[2] not in (3, 4)
    )


def normalise_to_int16(im, im_min, im_max):
    return ((im - im_min) / (im_max - im_min) * np.iinfo(np.int16).max).astype(np.int16)


def write_whole_copy(path, copy_path, use_sktiff, open_tiff):
    if use_sktiff:
        im = tifffile.imread(path)
    else:
        tif, _ = open_tiff(path, use_sktiff)
        im = tif.data()

    if im.dtype.type == np.float32:
        im = normalise_to_int16(im, im.min(), im.max())

    tifffile.imwrite(copy_path, im)


def iter_batches(path, nframes, batch_size, use_sktiff, open_tiff):
    """Frames [i, i + batch_size) of the file, with the reader its whole read uses."""
    if use_sktiff:
        with tifffile.TiffFile(path) as tif:
            for i in range(0, nframes, batch_size):
                yield tif.asarray(key=range(i, min(i + batch_size, nframes)))
    else:
        tif, _ = open_tiff(path, use_sktiff)
        try:
            for i in range(0, nframes, batch_size):
                yield tif.data(beg=i, end=min(i + batch_size, nframes))
        finally:
            tif.close()


def write_normalised_copy(path, copy_path, shape, use_sktiff, open_tiff):
    """
    The whole-file copy's int16 normalisation, one batch in memory.

    Two passes: the file's min / max, then each batch scaled with them. The
    operations are element-wise with the same float32 scalars, so the pixels are
    those of the whole-array expression; np.minimum / np.maximum propagate NaN
    as min() / max() do. tifffile picks BigTIFF from `data.nbytes`, which a
    frame iterator does not have, so the choice is made here the same way.
    """
    nframes, Ly, Lx = shape
    batch_size = max(1, BATCH_BYTES // (Ly * Lx * np.dtype(np.float32).itemsize))

    def batches():
        for batch in iter_batches(path, nframes, batch_size, use_sktiff, open_tiff):
            yield batch.reshape(-1, Ly, Lx)

    im_min = im_max = None
    for batch in batches():
        if im_min is None:
            im_min, im_max = batch.min(), batch.max()
        else:
            im_min = np.minimum(im_min, batch.min())
            im_max = np.maximum(im_max, batch.max())

    def frames():
        for batch in batches():
            yield from normalise_to_int16(batch, im_min, im_max)

    tmp_path = f"{copy_path}.partial"
    try:
        tifffile.imwrite(
            tmp_path,
            frames(),
            shape=shape,
            dtype=np.int16,
            bigtiff=nframes * Ly * Lx * np.dtype(np.int16).itemsize > BIGTIFF_BYTES,
        )
        os.replace(tmp_path, copy_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
