import numpy as np

from studio.app.common.dataclass.timeseries import TimeSeriesData


class BehaviorData(TimeSeriesData):
    """Time-major, (time, columns); a 1D input is one column."""

    def __init__(self, data, std=None, index=None, params=None, file_name="behavior"):
        data = np.asarray(data)
        if data.ndim == 1:
            data = data[:, np.newaxis]
        if std is not None and np.ndim(std) == 1:
            std = np.asarray(std)[:, np.newaxis]
        super().__init__(
            data=data,
            std=std,
            index=np.arange(data.shape[0]) if index is None else index,
            cell_numbers=range(data.shape[1]),
            params=params,
            file_name=file_name,
        )
        self.data = data  # the parent reshapes empty data as (rows, time)

    def _records(self, array):
        return np.asarray(array).T
