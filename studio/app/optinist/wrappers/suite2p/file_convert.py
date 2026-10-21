import os

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.dataclass import ImageData
from studio.app.optinist.dataclass import Suite2pData

logger = AppLogger.get_logger()


def suite2p_file_convert(
    image: ImageData, output_dir: str, params: dict = None, **kwargs
) -> dict(ops=Suite2pData):
    import numpy as np
    import tifffile
    from suite2p import default_db, default_settings, io
    from suite2p.io.tiff import open_tiff, use_sktiff_reader

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start suite2p_file_convert: %s", function_id)

    params = params or {}
    force_sktiff = bool(params.get("force_sktiff", False))
    batch_size = params.get("batch_size", 500)

    data_name_list = []
    for file_path in image.path:
        use_sktiff = force_sktiff or use_sktiff_reader(file_path, batch_size=batch_size)

        tif, _ = open_tiff(file_path, use_sktiff)
        if use_sktiff:
            im = tifffile.imread(file_path)
        else:
            im = tif.data()

        if im.dtype.type == np.float32:
            im = (im - im.min()) / (im.max() - im.min())
            im = (im * np.iinfo(np.int16).max).astype(np.int16)

        file_path = join_filepath([output_dir, os.path.basename(file_path)])
        tifffile.imwrite(file_path, im)
        data_name_list.append(os.path.basename(file_path))

    logger.debug(data_name_list)
    db = {
        **default_db(),
        "data_path": [output_dir],
        "file_list": data_name_list,
        "save_path0": output_dir,
        "save_folder": "suite2p",
        "force_sktiff": force_sktiff,
        "batch_size": batch_size,
    }
    db["file_list"], db["first_files"] = io.get_file_list(db)
    dbs = io.init_dbs(db)

    # write data.bin, db.npy and settings.npy under save_path0/suite2p/plane0
    with open(dbs[0]["reg_file"], "wb") as reg_file:
        ops = io.tiff_to_binary(dbs, default_settings(), [reg_file], None)[0]

    info = {
        "meanImg": ImageData(
            ops["meanImg"], output_dir=output_dir, file_name="meanImg"
        ),
        "ops": Suite2pData(ops, file_name="ops"),
    }

    return info
