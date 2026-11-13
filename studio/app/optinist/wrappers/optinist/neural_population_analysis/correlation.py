import numpy as np

from studio.app.common.core.experiment.experiment import ExptOutputPathIds
from studio.app.common.core.logger import AppLogger
from studio.app.common.dataclass import HeatMapData
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import FluoData, IscellData

logger = AppLogger.get_logger()


def cluster_order(corr, flat):
    """
    Dendrogram leaf order from average linkage on 1 - r, constant cells last.
    """
    from scipy.cluster.hierarchy import leaves_list, linkage
    from scipy.spatial.distance import squareform

    rows = np.setdiff1d(np.arange(len(corr)), flat)
    if len(rows) < 2:
        return np.concatenate([rows, flat]).astype(int)
    distance = 1 - corr[np.ix_(rows, rows)]
    np.fill_diagonal(distance, 0)
    distance = np.clip((distance + distance.T) / 2, 0, 2)
    leaves = leaves_list(linkage(squareform(distance), method="average"))
    return np.concatenate([rows[leaves], flat]).astype(int)


def correlation(
    neural_data: FluoData,
    output_dir: str,
    iscell: IscellData = None,
    params: dict = None,
    **kwargs,
) -> dict(corr=HeatMapData):
    import numpy as np

    function_id = ExptOutputPathIds(output_dir).function_id
    logger.info("start correlation: %s", function_id)

    neural_data = neural_data.data

    # X must be (cell, time); iscell picks rows
    if params["transpose"]:
        X = neural_data.transpose()
    else:
        X = neural_data

    ind = None
    if iscell is not None:
        iscell = iscell.data
        ind = np.where(iscell > 0)[0]
        assert len(ind) > 0, "iscell marks no ROI as a cell, nothing to correlate"
        X = X[ind, :]

    num_cell = X.shape[0]

    # calculate correlation
    # exact, unlike std == 0; a non-finite sample makes its whole row NaN
    flat = np.where((np.ptp(X, axis=1) == 0) | ~np.isfinite(X).all(axis=1))[0]
    if len(flat):
        rois = ind[flat] if ind is not None else flat
        logger.warning(
            "correlation: %d ROI(s) have a constant or non-finite trace, "
            "their rows and columns are NaN: %s",
            len(flat),
            rois.tolist(),
        )
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.atleast_2d(np.corrcoef(X))
    corr[flat, :] = np.nan
    corr[:, flat] = np.nan
    for i in range(num_cell):
        corr[i, i] = np.nan

    rois = ind if ind is not None else np.arange(num_cell)
    order = np.arange(num_cell)
    labels = ind
    if params.get("sort_by_cluster", False):
        order = cluster_order(corr, flat)
        labels = rois[order]

    # NWB追加
    nwbfile = {}
    nwbfile[NWBDATASET.POSTPROCESS] = {
        function_id: {
            "corr": corr,
            "order": rois[order].astype(float),
        }
    }

    info = {
        "corr": HeatMapData(
            corr[np.ix_(order, order)],
            columns=labels,
            index=labels,
            file_name="corr",
            meta=PlotMetaData(xaxis_type="category", yaxis_type="category"),
        ),
        "nwbfile": nwbfile,
    }

    return info
