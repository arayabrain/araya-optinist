from dataclasses import dataclass
from enum import Enum

from studio.app.common.core.compat import StrEnum
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

# File sync patterns for selective sync
ESSENTIAL_SYNC_PATTERNS = (".yaml", ".yml", ".json")
LARGE_FILE_PATTERNS = tuple(ACCEPT_FILE_EXT.ALL_EXT.value + [".pkl"])
# Visualization mode: JSON for timeseries data, TIFF for images,
# YAML for snakemake config
VISUALIZATION_SYNC_PATTERNS = (".json", ".tif", ".tiff", ".yaml")

# Thumbnail files for fast DataView loading
# These are small PNG images generated from input TIFFs and ROI data
THUMBNAIL_FILE_PATTERNS = ("input_thumb.png", "roi_thumb.png", "_thumb.png")


# Metadata cache filenames for input data
class MetadataCacheFile(StrEnum):
    IMAGE_SHAPE = ".image_shape.json"
    HDF5_STRUCTURE = ".hdf5_structure.json"
    MAT_STRUCTURE = ".mat_structure.json"
