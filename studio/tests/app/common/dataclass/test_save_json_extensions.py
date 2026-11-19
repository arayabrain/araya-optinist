"""Every file save_json() writes when a node is observed must reach other
hosts: the finalization upload sends the OBSERVATION_UPLOAD_PATTERNS files,
and the on-demand sync downloads the VISUALIZATION_SYNC_PATTERNS ones."""

import importlib
import inspect
import pkgutil

import numpy as np
import pytest

import studio.app.common.dataclass as common_dataclass
import studio.app.optinist.dataclass as optinist_dataclass
from studio.app.common.dataclass import (
    BarData,
    BaseData,
    CsvData,
    HeatMapData,
    HistogramData,
    HTMLData,
    ImageData,
    LineData,
    PieData,
    PolarData,
    ScatterData,
    TimeSeriesData,
)
from studio.app.common.schemas.outputs import PlotMetaData
from studio.app.const import OBSERVATION_UPLOAD_PATTERNS, VISUALIZATION_SYNC_PATTERNS
from studio.app.optinist.dataclass import (
    BehaviorData,
    CaimanCnmfData,
    EditRoiData,
    FluoData,
    IscellData,
    LccdData,
    RoiData,
    SpikingActivityData,
    Suite2pData,
)
from studio.app.optinist.dataclass.microscope import MicroscopeData
from studio.app.optinist.dataclass.roi import RoiMasks

PACKAGES = (common_dataclass, optinist_dataclass)
META = PlotMetaData(title="sample")
RNG = np.random.default_rng(0)


def rows():
    return RNG.random((3, 12))


# A small instance of each dataclass. The argument is a folder for the files an
# instance writes when it is made, which are not save_json() output.
SAMPLES = {
    BarData: lambda d: BarData(RNG.random((3, 2)), meta=META),
    CsvData: lambda d: CsvData(RNG.random((4, 3)), params={}, meta=META),
    HeatMapData: lambda d: HeatMapData(RNG.random((4, 5)), meta=META),
    HistogramData: lambda d: HistogramData(RNG.random(10), meta=META),
    HTMLData: lambda d: HTMLData("<p>sample</p>", meta=META),
    ImageData: lambda d: ImageData(
        RNG.random((3, 6, 6)).astype(np.float32), output_dir=d, meta=META
    ),
    LineData: lambda d: LineData(RNG.random((2, 4)), columns=list(range(4))),
    PieData: lambda d: PieData(RNG.random(3), labels=["a", "b", "c"]),
    PolarData: lambda d: PolarData(RNG.random((2, 4)), thetas=[0, 90, 180, 270]),
    ScatterData: lambda d: ScatterData(RNG.random((10, 2)), meta=META),
    TimeSeriesData: lambda d: TimeSeriesData(rows(), std=rows(), meta=META),
    BehaviorData: lambda d: BehaviorData(rows().T, std=rows().T),
    FluoData: lambda d: FluoData(rows(), std=rows(), meta=META),
    SpikingActivityData: lambda d: SpikingActivityData(rows(), meta=META),
    IscellData: lambda d: IscellData(np.ones(3)),
    RoiData: lambda d: RoiData(RNG.random((6, 6)), output_dir=d, meta=META),
    EditRoiData: lambda d: EditRoiData(
        RNG.random((3, 6, 6)), RoiMasks.from_pixels((6, 6), [[0, 1]])
    ),
    MicroscopeData: lambda d: MicroscopeData("sample.nd2"),
    Suite2pData: lambda d: Suite2pData({}),
    CaimanCnmfData: lambda d: CaimanCnmfData({}),
    LccdData: lambda d: LccdData({}),
}


def defined_dataclasses() -> set:
    """Concrete BaseData subclasses defined in the dataclass packages"""
    for package in PACKAGES:
        for module in pkgutil.walk_packages(package.__path__, f"{package.__name__}."):
            importlib.import_module(module.name)
    prefixes = tuple(f"{package.__name__}." for package in PACKAGES)

    found, pending = set(), [BaseData]
    while pending:
        for cls in pending.pop().__subclasses__():
            pending.append(cls)
            if cls.__module__.startswith(prefixes) and not inspect.isabstract(cls):
                found.add(cls)
    return found


def test_every_dataclass_has_a_sample():
    # A new dataclass needs a sample, so the files it writes are checked below
    assert defined_dataclasses() == set(SAMPLES)


@pytest.mark.parametrize("cls", list(SAMPLES), ids=lambda cls: cls.__name__)
def test_save_json_writes_only_uploaded_files(cls, tmp_path):
    made_dir, json_dir = tmp_path / "made", tmp_path / "node"
    made_dir.mkdir()
    json_dir.mkdir()

    SAMPLES[cls](str(made_dir)).save_json(str(json_dir))

    written = [p.relative_to(json_dir) for p in json_dir.rglob("*") if p.is_file()]
    assert [
        str(p) for p in written if not p.name.endswith(OBSERVATION_UPLOAD_PATTERNS)
    ] == []
    # The upload's glob skips hidden files and folders
    assert [str(p) for p in written if any(s.startswith(".") for s in p.parts)] == []


def test_uploaded_extensions_are_downloaded():
    assert set(OBSERVATION_UPLOAD_PATTERNS) <= set(VISUALIZATION_SYNC_PATTERNS)
