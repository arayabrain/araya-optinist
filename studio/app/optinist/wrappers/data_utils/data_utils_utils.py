import numpy as np

from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass.bar import BarData
from studio.app.common.dataclass.csv import CsvData
from studio.app.common.dataclass.heatmap import HeatMapData
from studio.app.common.dataclass.image import ImageData
from studio.app.common.dataclass.scatter import ScatterData
from studio.app.common.dataclass.timeseries import TimeSeriesData
from studio.app.optinist.dataclass.behavior import BehaviorData
from studio.app.optinist.dataclass.fluo import FluoData
from studio.app.optinist.dataclass.iscell import IscellData
from studio.app.optinist.dataclass.roi import RoiData


def _rows_as_column(processed_data, index):
    """An indexed 1D vector is one value per row, not one row."""
    if processed_data.ndim == 1 and index is not None:
        if len(index) == len(processed_data):
            return processed_data[:, None]
    return processed_data


def return_as_data_type(data, processed_data, output_dir, file_name, **kwargs):
    """Helper function to return the correct data type with processed data."""
    logger = AppLogger.get_logger()

    # Extract kwargs
    std = kwargs.get("std", None)
    index = kwargs.get("index", None)
    output_type = kwargs.get("output_type", None)
    if isinstance(output_type, str) and output_type.strip() == "":
        output_type = None

    logger.debug(f"Input data type: {type(data).__name__}")
    if output_type is None:
        logger.debug("No output_type specified, using input data type for output")

    # Determine output type and key based on input type or explicit output_type
    if output_type in ["behaviors_data", "BehaviorData", "CsvData"] or (
        output_type is None and isinstance(data, (BehaviorData, CsvData))
    ):
        logger.debug("Processing as BehaviorData or CsvData")
        result = BehaviorData(
            data=processed_data,
            std=std,
            index=index,
            params={},
            file_name=file_name,
        )
        output_key = "behaviors_data"

    elif output_type in ["neural_data", "FluoData"] or (
        output_type is None and isinstance(data, FluoData)
    ):
        logger.debug("Processing as FluoData")
        result = FluoData(
            data=processed_data,
            std=std,
            index=index,
            params={},
            file_name=file_name,
            meta=data.meta if hasattr(data, "meta") else None,
        )
        output_key = "neural_data"

    elif output_type in ["timeseries_data", "TimeSeriesData"] or (
        output_type is None and isinstance(data, TimeSeriesData)
    ):
        logger.debug("Processing as TimeSeriesData")
        result = TimeSeriesData(
            data=processed_data,
            std=std,
            sem=kwargs.get("sem", None),
            index=index,
            cell_numbers=kwargs.get("cell_numbers", None),
            params={},
            file_name=file_name,
            meta=data.meta if hasattr(data, "meta") else None,
        )
        output_key = "timeseries"

    elif output_type in ["image_data", "ImageData", "image"] or (
        output_type is None and isinstance(data, ImageData)
    ):
        logger.debug("Processing as ImageData")
        result = ImageData(
            data=processed_data,
            output_dir=output_dir,
            file_name=file_name,
            meta=data.meta if hasattr(data, "meta") else None,
        )
        output_key = "image"

    elif output_type in ["iscell_data", "IscellData", "iscell"] or (
        output_type is None and isinstance(data, IscellData)
    ):
        result = IscellData(
            data=processed_data,
            file_name=file_name,
        )
        output_key = "iscell"

    elif output_type in ["roi_data", "RoiData", "roi"] or (
        output_type is None and isinstance(data, RoiData)
    ):
        result = RoiData(
            data=processed_data,
            output_dir=output_dir,
            file_name=file_name,
            meta=data.meta if hasattr(data, "meta") else None,
        )
        output_key = "roi"

    elif output_type in ["bar_data", "BarData", "bar"] or (
        output_type is None and isinstance(data, BarData)
    ):
        result = BarData(
            data=_rows_as_column(processed_data, index),
            index=index,
            file_name=file_name,
            meta=data.meta if hasattr(data, "meta") else None,
        )
        output_key = "bar"

    elif output_type in ["heatmap_data", "HeatMapData", "heatmap"] or (
        output_type is None and isinstance(data, HeatMapData)
    ):
        heatmap_data = np.atleast_2d(_rows_as_column(processed_data, index))
        columns = getattr(data, "columns", None)
        if columns is not None and len(columns) != heatmap_data.shape[1]:
            columns = None
        result = HeatMapData(
            data=heatmap_data,
            columns=columns,
            index=index,
            file_name=file_name,
            meta=data.meta if hasattr(data, "meta") else None,
        )
        output_key = "heatmap"

    elif output_type in ["scatter_data", "ScatterData", "scatter"] or (
        output_type is None and isinstance(data, ScatterData)
    ):
        # ScatterData transposes on construction; pre-transpose so the stored
        # orientation round-trips
        result = ScatterData(
            data=processed_data.T,
            file_name=file_name,
            meta=data.meta if hasattr(data, "meta") else None,
        )
        output_key = "scatter"

    else:
        raise ValueError(
            f"Unsupported data type '{type(data).__name__}' "
            f"(output_type='{output_type}'); supported input types are "
            "BehaviorData, CsvData, FluoData, TimeSeriesData, ImageData, "
            "IscellData, RoiData, BarData, HeatMapData, and ScatterData"
        )

    logger.debug(f"Created {type(result).__name__} with output key: {output_key}")
    return {output_key: result}
