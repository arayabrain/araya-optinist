import contextlib
import os
import sys
import types

import imageio
import numpy as np
import pytest
import tifffile
from scipy.sparse import csc_matrix

from studio.app.common.dataclass import ImageData
from studio.app.optinist.dataclass.roi import open_edit_images
from studio.app.optinist.wrappers.caiman import cnmf
from studio.app.optinist.wrappers.caiman import cnmf_multisession as ms


def write_movie(path, frames=23, dtype=np.uint16, frame_shape=(6, 7)):
    rng = np.random.default_rng(frames)
    movie = rng.normal(300, 60, (frames, *frame_shape)).clip(0).astype(dtype)
    movie[:, :3, :3] += (rng.random((frames, 1, 1)) * 50).astype(dtype)  # a cell
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(path, movie, photometric="minisblack")
    return movie


def caiman_load_mean(path):
    """caiman.load(path).mean(0) for a TIFF stack (CaImAn 1.11's tif branch)."""
    with tifffile.TiffFile(path) as tif:
        return np.squeeze(tif.asarray()).astype(np.float32).mean(0)


@pytest.mark.parametrize("dtype", [np.uint16, np.int16, np.float32, np.float64])
@pytest.mark.parametrize("batch_bytes", [1, 5 * 6 * 7 * 4, 64 << 20])
def test_session_template_is_caimans_load_mean(
    tmp_path, monkeypatch, dtype, batch_bytes
):
    path = str(tmp_path / "part.tif")
    write_movie(path, frames=37, dtype=dtype)
    monkeypatch.setattr(ms, "BATCH_BYTES", batch_bytes)

    template = ms.session_template(path)

    expected = caiman_load_mean(path)
    assert template.dtype == expected.dtype == np.float32
    assert template.tobytes() == expected.tobytes()


def test_frame_stack_reads_the_files_joined_in_time(tmp_path):
    movies = [
        write_movie(str(tmp_path / f"p{n}.tif"), frames=f)
        for n, f in enumerate([4, 3, 5])
    ]
    whole = np.concatenate(movies)
    stack = ms.FrameStack([str(tmp_path / f"p{n}.tif") for n in range(3)])

    assert stack.shape == whole.shape
    for key in [slice(0, 4), slice(1, 3), slice(2, 9), slice(0, 12), slice(10, 99)]:
        np.testing.assert_array_equal(stack[key], whole[key])
    assert np.shares_memory(stack[slice(4, 7)], stack.files[1])  # inside a file


def test_correlation_over_the_parts_equals_the_whole_movie(tmp_path, monkeypatch):
    path = str(tmp_path / "input" / "movie.tif")
    movie = write_movie(path, frames=31)
    parts = ImageData(path).split_image(str(tmp_path / "node"), lengths=[3, 17, 11])
    # chunks of 4 frames: some span two parts
    monkeypatch.setattr(cnmf, "CORRELATION_CHUNK_ELEMENTS", 4 * 6 * 7)

    with np.errstate(invalid="ignore"):
        expected = cnmf.correlation_image(ImageData(path).data)
        actual = cnmf.correlation_image(ms.FrameStack(parts))

    for got, want in zip(actual, expected):  # the image and the mean
        assert got.tobytes() == want.tobytes()
    assert movie.dtype == tifffile.memmap(parts[0], mode="r").dtype


@pytest.fixture
def caiman(monkeypatch):
    """CaImAn as far as caiman_cnmf_multisession uses it; records the templates."""
    calls = {"memmaps": [], "templates": None}

    class CNMFParams:
        def change_params(self, params):
            pass

    class CNMF:
        def __init__(self, **kwargs):
            pass

        def fit(self, images):
            T, d1, d2 = images.shape
            footprints = np.zeros((d1 * d2, 2))
            footprints[[0, d1 * d2 - 1], [0, 1]] = 1
            C = np.asarray(images).reshape(T, -1)[:, [0, -1]].T.astype(np.float64)
            self.estimates = types.SimpleNamespace(A=csc_matrix(footprints), C=C)
            return self

    def register_multisession(A, dims, templates, **kwargs):
        calls["templates"] = [np.array(t) for t in templates]
        return None, np.array([[0.0, 0.0], [1.0, 1.0]]), None

    def memmap_frames_filename(base, dims, T, order):
        return f"{base}_d1_{dims[0]}_d2_{dims[1]}_d3_1_order_{order}_frames_{T}.mmap"

    modules = {
        "caiman": {},
        "caiman.mmapping": {"prepare_shape": lambda s: tuple(np.uint64(n) for n in s)},
        "caiman.paths": {"memmap_frames_filename": memmap_frames_filename},
        "caiman.base": {},
        "caiman.base.rois": {"register_multisession": register_multisession},
        "caiman.source_extraction": {},
        "caiman.source_extraction.cnmf": {"cnmf": types.SimpleNamespace(CNMF=CNMF)},
        "caiman.source_extraction.cnmf.params": {"CNMFParams": CNMFParams},
    }
    for name, attributes in modules.items():
        module = types.ModuleType(name)
        vars(module).update(attributes)
        monkeypatch.setitem(sys.modules, name, module)

    fill = ms.util_get_image_memmap_from_files

    def record_fill(function_id, paths, file_path, *args, **kwargs):
        calls["memmaps"].append((paths, file_path))
        return fill(function_id, paths, file_path, *args, **kwargs)

    @contextlib.contextmanager
    def single_process(n_processes):
        yield None, 1

    monkeypatch.setattr(ms, "util_get_image_memmap_from_files", record_fill)
    monkeypatch.setattr(ms, "caiman_cluster", single_process)
    monkeypatch.setattr(ms, "util_download_model_files", lambda: None)
    monkeypatch.setattr(  # skimage is not in the app image
        ms,
        "get_roi",
        lambda A, roi_thr, method, swap, dims: [
            A[:, [i]].toarray().reshape(dims, order="F") > 0 for i in range(A.shape[1])
        ],
    )
    monkeypatch.setattr(
        ms, "caiman_dff", lambda e, rows, n, w: np.zeros((len(rows), n), np.float32)
    )
    return calls


@pytest.mark.parametrize("where", ["input outside the experiment", "in the experiment"])
def test_multisession_reads_no_whole_movie_and_keeps_a_reference(
    tmp_path, monkeypatch, caiman, where
):
    experiment = tmp_path / "output" / "default" / "uid"
    if where == "in the experiment":
        path = str(experiment / "caiman_mc_x" / "tiff" / "mc_images" / "mc_images.tif")
    else:
        path = str(tmp_path / "input" / "default" / "movie.tif")
    movie = write_movie(path, frames=31)
    output_dir = experiment / "cnmf_multisession_x"
    output_dir.mkdir(parents=True)

    def no_full_read(*args, **kwargs):
        raise AssertionError("the whole movie was read")

    monkeypatch.setattr(ImageData, "data", property(no_full_read))
    monkeypatch.setattr(imageio, "volread", no_full_read)
    info = ms.caiman_cnmf_multisession(
        ImageData(path), str(output_dir), params={"n_reg_files": 2}
    )
    monkeypatch.undo()

    name = os.path.splitext(os.path.basename(path))[0]
    parts = sorted(str(p) for p in (output_dir / "image_split" / name).iterdir())
    assert [os.path.basename(p) for p in parts] == [f"{name}_0.tif", f"{name}_1.tif"]
    assert [paths for paths, _ in caiman["memmaps"]] == parts  # memmaps removed
    for template, part in zip(caiman["templates"], parts):
        assert template.tobytes() == caiman_load_mean(part).tobytes()

    with np.errstate(invalid="ignore"):
        Cn, mean = cnmf.correlation_image(movie)
    Cn[np.isnan(Cn)] = 0
    images = tifffile.imread(info["images"].path[0])
    assert images.tobytes() == np.array(Cn * 255, dtype=np.uint8).tobytes()
    np.testing.assert_array_equal(info["meanImg"].data, mean.astype(np.float32))

    reference = info["edit_roi_data"].images
    assert isinstance(reference, ImageData) and reference.fingerprint
    referenced = parts if where == "input outside the experiment" else [path]
    assert reference.path == [os.path.relpath(p, experiment) for p in referenced]
    np.testing.assert_array_equal(open_edit_images(reference, str(output_dir)), movie)
