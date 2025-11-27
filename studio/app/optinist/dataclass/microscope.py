import os

from studio.app.common.dataclass.base import BaseData
from studio.app.optinist.microscopes.IsxdReader import IsxdReader
from studio.app.optinist.microscopes.ND2Reader import ND2Reader
from studio.app.optinist.microscopes.OIRReader import OIRReader
from studio.app.optinist.microscopes.ThorlabsReader import ThorlabsReader


class MicroscopeData(BaseData):
    def __init__(self, path: str, file_name="microscope"):
        super().__init__(file_name)
        self._path = path
        self.json_path = None

    @property
    def path(self):
        """
        Get file path, ensuring it is available locally in batch mode.

        When running in AWS Batch, microscope files may not be downloaded
        by Snakemake's storage plugin. This property ensures they are
        retrieved from S3 before being accessed.
        """
        from studio.app.common.core.cloud_batch.storage_utils import (
            ensure_file_available_to_batch,
        )

        return ensure_file_available_to_batch(self._path)

    def save_json(self, json_dir):
        pass

    @property
    def reader(self):
        # self.path property already ensures file is available
        path = self.path

        ext = os.path.splitext(path)[1]
        if ext == ".nd2":
            reader = ND2Reader()
        elif ext == ".oir":
            assert OIRReader.is_available(), "OIRReader is not available."
            reader = OIRReader()
        elif ext == ".isxd":
            reader = IsxdReader()
        elif ext == ".thor.zip":
            path = os.path.dirname(path)
            reader = ThorlabsReader()
        else:
            raise Exception(f"Unsupported file type: {ext}")

        reader.load(path)
        return reader

    def set_data(self, data):
        self.data = data
