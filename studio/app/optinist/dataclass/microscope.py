import os

from studio.app.common.dataclass.base import BaseData
from studio.app.common.dataclass.image import ImageData
from studio.app.optinist.microscopes.IsxdReader import IsxdReader
from studio.app.optinist.microscopes.ND2Reader import ND2Reader
from studio.app.optinist.microscopes.OIRReader import OIRReader
from studio.app.optinist.microscopes.ThorlabsReader import ThorlabsReader


class MicroscopeData(BaseData):
    def __init__(self, path: str, file_name="microscope"):
        super().__init__(file_name)
        self.path = path
        self.json_path = None

    def save_json(self, json_dir):
        pass

    @property
    def reader(self):
        ext = os.path.splitext(self.path)[1]
        # ".thor.zip" has two suffixes, so splitext gives ".zip". ThorlabsReader
        # takes the archive itself and extracts it.
        if self.path.endswith(".thor.zip"):
            reader = ThorlabsReader()
        elif ext == ".nd2":
            reader = ND2Reader()
        elif ext == ".oir":
            assert OIRReader.is_available(), "OIRReader is not available."
            reader = OIRReader()
        elif ext == ".isxd":
            reader = IsxdReader()
        else:
            raise Exception(f"Unsupported file type: {ext}")

        reader.load(self.path)
        return reader

    def set_image(self, image: ImageData):
        """Keep the converted stack as a reference; its frames are read only
        when asked for (NWB with save_raw_image_to_nwb)."""
        self.image = image

    @property
    def data(self):
        # An older build stored the frames themselves
        if "data" in self.__dict__:
            return self.__dict__["data"]
        return self.image.data

    @data.setter
    def data(self, frames):
        # Experiment copy rewrites every attribute with setattr
        self.__dict__["data"] = frames
