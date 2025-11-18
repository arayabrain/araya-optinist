from dataclasses import dataclass
from enum import Enum

from studio.app.common.core.utils.config_handler import get_env_var


@dataclass
class FILETYPE:
    IMAGE: str = "image"
    CSV: str = "csv"
    HDF5: str = "hdf5"
    BEHAVIOR: str = "behavior"
    MATLAB: str = "matlab"
    MICROSCOPE: str = "microscope"


class ACCEPT_FILE_EXT(Enum):
    TIFF_EXT = [".tif", ".tiff", ".TIF", ".TIFF"]
    CSV_EXT = [".csv"]
    HDF5_EXT = [".hdf5", ".h5", ".nwb", ".HDF5", ".NWB"]
    MATLAB_EXT = [".mat"]
    MICROSCOPE_EXT = [".nd2", ".oir", ".isxd", ".thor.zip"]

    ALL_EXT = TIFF_EXT + CSV_EXT + HDF5_EXT + MATLAB_EXT + MICROSCOPE_EXT


ORIGINAL_DATA_EXT = ".orig"

NOT_DISPLAY_ARGS_LIST = ["params", "output_dir", "nwbfile", "kwargs"]

DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Default organization ID for multi-user cloud deployment
DEFAULT_ORGANIZATION_ID = 1

# Default is set for local development, override with FRONTEND_URL environment variable
FRONTEND_URL = get_env_var("FRONTEND_URL", default="http://localhost:3000")
