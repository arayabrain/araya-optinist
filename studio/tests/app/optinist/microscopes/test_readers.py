"""Every vendor reader on a real file. The Olympus and Nikon samples are public
files fetched into samples/ on first run; the Inscopix movie beside this file
was written by the vendor's isx 2.0.1 SDK; the Thorlabs scan is written here."""
import io
import itertools
import os
import platform
import shutil
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pytest
import tifffile

from studio.app.dir_path import DIRPATH
from studio.app.optinist.microscopes.IsxdReader import IsxdReader
from studio.app.optinist.microscopes.MicroscopeDataReaderBase import (
    OMEDataModel,
    unpack_microscope_libs,
)
from studio.app.optinist.microscopes.ND2Reader import ND2Reader
from studio.app.optinist.microscopes.OIRReader import OIRReader
from studio.app.optinist.microscopes.ThorlabsReader import ThorlabsReader

HERE = Path(__file__).parent
SAMPLES = HERE / "samples"
# CC BY 4.0, (c) Chin Hung, zenodo record 14417737
OIR_URL = "https://downloads.openmicroscopy.org/images/Olympus-OIR/imagesc-105684/1202-interval_10sec_sequence_frame.oir"  # noqa: E501
# The nd2 package's (BSD-3) test-data release
ND2_URL = "https://github.com/tlambert03/nd2/releases/download/test-data/dims_t3c2y32x32.nd2"  # noqa: E501

linux_x86_64 = pytest.mark.skipif(
    (platform.system(), platform.machine()) != ("Linux", "x86_64"),
    reason="the SDK ships a Linux x86_64 library only",
)


def shipped(reader):
    """Skip when libs.zip does not ship the reader's library; a shipped library
    that will not unpack or load is a failure."""
    member = reader.SDK_LIBRARY_FILES["Linux"]["main"].lstrip("/")
    if member not in zipfile.ZipFile(DIRPATH.MICROSCOPE_LIB_ZIP).namelist():
        pytest.skip(f"{member} is not in libs.zip")


def sample(url):
    path = SAMPLES / url.rsplit("/", 1)[1]
    if not path.exists():
        SAMPLES.mkdir(exist_ok=True)
        try:
            deadline = time.monotonic() + 120
            with urllib.request.urlopen(url, timeout=60) as response:
                with open(f"{path}.part", "wb") as f:
                    while chunk := response.read1(1 << 16):
                        if time.monotonic() > deadline:
                            raise TimeoutError("still downloading after 120 s")
                        f.write(chunk)
        except OSError as e:
            pytest.skip(f"could not download {url}: {e}")
        os.replace(f"{path}.part", path)
    return str(path)


def test_sample_skips_a_download_that_outlasts_its_deadline(tmp_path, monkeypatch):
    monkeypatch.setitem(globals(), "SAMPLES", tmp_path)
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout: io.BytesIO(b"x")
    )
    monkeypatch.setattr(time, "monotonic", itertools.count(step=121).__next__)
    with pytest.raises(pytest.skip.Exception, match="after 120 s"):
        sample("https://example.org/slow.oir")
    assert not (tmp_path / "slow.oir").exists()


@linux_x86_64
def test_oir_reader():
    shipped(OIRReader)
    reader = OIRReader()
    reader.load(sample(OIR_URL))
    ome = reader.ome_metadata
    assert (ome.size_x, ome.size_y, ome.size_t, ome.size_z, ome.size_c) == (
        512,
        512,
        8,
        1,
        2,
    )
    assert (ome.depth, ome.imaging_rate, ome.objective_model) == (
        16,
        0.92,
        "UPLXAPO10X",
    )
    lab = reader.lab_specific_metadata
    assert (lab["Loops"], lab["TInterval"], lab["SystemName"]) == (8, 10.0, "FV4000")
    stack = reader.get_image_stacks()
    assert stack.shape == (2, 8, 512, 512) and stack.dtype == np.uint16
    assert (stack.min(), stack.max()) == (0, 10155)


@linux_x86_64
def test_nd2_reader():
    shipped(ND2Reader)
    reader = ND2Reader()
    reader.load(sample(ND2_URL))
    ome = reader.ome_metadata
    assert (ome.size_x, ome.size_y, ome.size_t, ome.size_z, ome.size_c) == (
        32,
        32,
        3,
        0,
        2,
    )
    assert (ome.depth, ome.imaging_rate, ome.objective_model) == (
        16,
        1.07,
        "Plan Fluor 10x Ph1 DLL",
    )
    lab = reader.lab_specific_metadata
    assert (lab["Type"], lab["Loops"], lab["uiSequenceCount"]) == ("2Dm", 3, 3)
    stack = reader.get_image_stacks()
    assert stack.shape == (2, 3, 32, 32) and stack.dtype == np.uint16


def test_unpack_publishes_the_sdk_directory_only_when_it_is_complete(
    tmp_path, monkeypatch
):
    with zipfile.ZipFile(tmp_path / "libs.zip", "w") as zf:
        zf.writestr("vendor/lib.so", b"x")
    monkeypatch.setattr(DIRPATH, "MICROSCOPE_LIB_ZIP", str(tmp_path / "libs.zip"))
    monkeypatch.setattr(DIRPATH, "MICROSCOPE_LIB_DIR", str(tmp_path / "libs"))
    unpack = shutil.unpack_archive

    def unpack_beside_the_target(archive, target):
        assert target != str(tmp_path / "libs") and not (tmp_path / "libs").exists()
        unpack(archive, target)

    monkeypatch.setattr(shutil, "unpack_archive", unpack_beside_the_target)
    unpack_microscope_libs()
    assert (tmp_path / "libs" / "vendor" / "lib.so").read_bytes() == b"x"

    # another process publishes between this one's unpack and its rename
    shutil.rmtree(tmp_path / "libs")
    rename = os.rename

    def rename_after_a_winner(src, dst):
        shutil.copytree(src, dst)
        rename(src, dst)

    monkeypatch.setattr(os, "rename", rename_after_a_winner)
    unpack_microscope_libs()
    assert (tmp_path / "libs" / "vendor" / "lib.so").read_bytes() == b"x"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["libs", "libs.zip"]


def test_isxd_reader():
    if not IsxdReader.is_available():
        pytest.skip("the isx package is not installed")
    reader = IsxdReader()
    reader.load(str(HERE / "movie_t5_y3_x4.isxd"))
    ome = reader.ome_metadata
    assert (ome.size_x, ome.size_y, ome.size_t) == (4, 3, 5)
    assert (ome.depth, ome.imaging_rate) == (16, 10.0)
    assert reader.original_metadata["timing"]["period_msec"] == 100
    stack = reader.get_image_stacks()
    assert stack.shape == (1, 5, 3, 4) and stack.dtype == np.uint16
    frame = np.arange(12, dtype=np.uint16).reshape(3, 4)
    np.testing.assert_array_equal(
        stack[0], frame + 100 * np.arange(5, dtype=np.uint16)[:, None, None]
    )


THOR_FRAMES = np.arange(3 * 2 * 4 * 5, dtype=np.uint16).reshape(3, 2, 4, 5)  # tcyx
THOR_OME = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06">'
    '<Image ID="Image:0" Name="scan" AcquiredDate="2024-01-02T03:04:05">'
    '<Pixels ID="Pixels:0" DimensionOrder="XYCZT" Type="uint16" SizeX="5" SizeY="4"'
    ' SizeC="2" SizeZ="1" SizeT="3" PhysicalSizeX="0.5" PhysicalSizeY="0.5">'
    '<Channel ID="Channel:0:0" SamplesPerPixel="1"/>'
    '<Channel ID="Channel:0:1" SamplesPerPixel="1"/>'
    "<TiffData/></Pixels></Image></OME>"
)


def write_thorlabs_scan(directory):
    """ThorImageLS's layout: an OME-TIFF whose Image carries AcquiredDate
    rather than OME's AcquisitionDate, beside Experiment.xml."""
    directory.mkdir()
    tifffile.imwrite(
        directory / "Image_0001_0001.tif",
        THOR_FRAMES,
        description=THOR_OME,
        metadata=None,
    )
    (directory / "Experiment.xml").write_text(
        '<ThorImageExperiment><LSM frameRate="30" /></ThorImageExperiment>'
    )


def assert_thorlabs(reader):
    ome = reader.ome_metadata
    assert (ome.size_x, ome.size_y, ome.size_t, ome.size_z, ome.size_c) == (
        5,
        4,
        3,
        1,
        2,
    )
    assert (ome.physical_sizex, ome.imaging_rate, ome.acquisition_date) == (
        0.5,
        30.0,
        "2024-01-02T03:04:05",
    )
    np.testing.assert_array_equal(
        reader.get_image_stacks(), THOR_FRAMES.transpose(1, 0, 2, 3)
    )


def test_thorlabs_reader_reads_a_scan_directory(tmp_path):
    write_thorlabs_scan(tmp_path / "scan")
    reader = ThorlabsReader()
    reader.load(str(tmp_path / "scan"))
    assert_thorlabs(reader)


def test_thorlabs_reader_extracts_a_thor_zip_and_removes_it_on_release(tmp_path):
    write_thorlabs_scan(tmp_path / "scan")
    archive = tmp_path / "scan.thor.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for file in (tmp_path / "scan").iterdir():
            zf.write(file, file.name)
    reader = ThorlabsReader()
    reader.load(str(archive))
    assert_thorlabs(reader)
    assert len(list(tmp_path.iterdir())) == 3
    del reader
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scan", "scan.thor.zip"]


def test_thorlabs_reader_closes_its_tiff_on_release(tmp_path):
    write_thorlabs_scan(tmp_path / "scan")
    reader = ThorlabsReader()
    reader.load(str(tmp_path / "scan"))
    (handle_tiff,) = reader.resource_handles
    del reader
    assert handle_tiff.filehandle.closed


def test_uint8_pixels_have_depth_8():
    assert OMEDataModel.get_depth_from_pixel_type("uint8") == 8


def test_reader_whose_library_fails_to_init_can_still_be_destroyed(monkeypatch):
    def fail():
        raise OSError("no library")

    monkeypatch.setattr(ThorlabsReader, "_init_library", lambda self: fail())
    reader = ThorlabsReader.__new__(ThorlabsReader)
    with pytest.raises(OSError):
        reader.__init__()
    reader.__del__()


@pytest.mark.parametrize("reader", [ND2Reader, OIRReader])
def test_reader_is_unavailable_without_a_library_path(reader, monkeypatch):
    monkeypatch.setattr(reader, "unpack_libs", staticmethod(lambda: None))
    monkeypatch.setattr(reader, "get_library_path", staticmethod(lambda: None))
    assert reader.is_available() is False
