from dataclasses import dataclass
from typing import Optional


@dataclass
class DatasetPaths:
    """Internal dataset paths for structured data formats (HDF5, MAT, etc.).

    When a new structured data type is added, add a field here.
    All functions in the thumbnail chain accept a single DatasetPaths parameter,
    so no other function signatures need to change.
    """

    hdf5_path: Optional[str] = None
    mat_path: Optional[str] = None
