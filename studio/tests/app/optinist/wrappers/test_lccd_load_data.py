"""lccd's LoadData reads frames when sliced instead of holding the movie, and
vacant ROI reads only the shape. Both must give what the whole-movie LoadData
gave, bit for bit."""
import sys
import types

import numpy as np
import pytest
import tifffile
from PIL import Image, TiffImagePlugin

from studio.app.common.dataclass import ImageData
from studio.app.optinist.wrappers.data_utils.vacant_roi import movie_shape, vacant_roi
from studio.app.optinist.wrappers.lccd import lccd_detection
from studio.app.optinist.wrappers.lccd.lccd_detection import LoadData, lccd_detect

FRAME_DIVIDER = 10


def whole_movie(path):
    """LoadData as it was: the whole movie, normalised out of place (the file
    is closed on errors too, which it was not)."""
    from PIL import Image

    with Image.open(path) as img_pile:
        num_page = img_pile.n_frames

        imgs = np.zeros((img_pile.size[0], img_pile.size[1], num_page))
        for i in range(num_page):
            img_pile.seek(i)
            imgs[:, :, i] = np.asarray(img_pile)

    maxval = np.max(imgs)
    minval = np.min(imgs)
    imgs = (imgs - minval) / (maxval - minval)

    return imgs


def whole_movie_means(D, roi):
    """The traces as lccd_detect computed them from the whole movie."""
    reshapedD = D.reshape([D.shape[0] * D.shape[1], D.shape[2]])
    timeseries = np.zeros([roi.shape[1], D.shape[2]])
    for i in range(roi.shape[1]):
        timeseries[i, :] = np.mean(reshapedD[roi[:, i] > 0, :], axis=0)
    return timeseries


def write(path, frames, dtype, side=16, shape=None):
    rng = np.random.default_rng(frames)
    movie = rng.normal(300, 120, (frames, *(shape or (side, side))))
    if np.issubdtype(dtype, np.integer):
        movie = movie.round()
    tifffile.imwrite(path, movie.astype(dtype))
    return str(path)


def random_rois(shape, n, rng):
    """(pixels, n) masks of 9 to 90 pixels each, as LCCD's area limits allow."""
    roi = np.zeros((shape[0] * shape[1], n), dtype=np.float32)
    for i in range(n):
        roi[rng.choice(roi.shape[0], rng.integers(9, 91), replace=False), i] = 1
    return roi


@pytest.fixture
def small_batches(monkeypatch):
    """3 frames of 16 x 16 per batch, so 37 frames end in a 1-frame tail."""
    monkeypatch.setattr(lccd_detection, "BATCH_BYTES", 3 * 16 * 16 * 8)


MOVIES = [
    ("uint16.tif", 37, np.uint16),  # an input file
    ("int16.tif", 37, np.int16),  # suite2p's mc_images; negative values too
    ("float32.tif", 37, np.float32),  # caiman_mc's mc_images
    ("one_frame.tif", 1, np.uint16),
]


@pytest.mark.parametrize("name, frames, dtype", MOVIES)
def test_slices_match_the_whole_movie(tmp_path, small_batches, name, frames, dtype):
    path = write(tmp_path / name, frames, dtype)
    expected = whole_movie(path)
    with LoadData(ImageData([path])) as D:
        assert D.shape == expected.shape
        for start in range(0, frames, FRAME_DIVIDER):
            window = slice(start, start + FRAME_DIVIDER)
            assert D[:, :, window].tobytes() == expected[:, :, window].tobytes()


@pytest.mark.parametrize("name, frames, dtype", MOVIES)
def test_roi_means_match_the_whole_movie(tmp_path, small_batches, name, frames, dtype):
    path = write(tmp_path / name, frames, dtype)
    expected_movie = whole_movie(path)
    roi = random_rois(expected_movie.shape, 12, np.random.default_rng(1))
    roi[:, 3] = 0  # an ROI without pixels: NaN on both sides
    with pytest.warns(RuntimeWarning), LoadData(ImageData([path])) as D:
        actual = D.roi_means(roi)
    with pytest.warns(RuntimeWarning):
        expected = whole_movie_means(expected_movie, roi)
    assert actual.tobytes() == expected.tobytes()


def test_no_roi_reads_no_frame(tmp_path, monkeypatch):
    path = write(tmp_path / "m.tif", 37, np.uint16)
    with LoadData(ImageData([path])) as D:
        monkeypatch.setattr(D, "_read", None)  # the scan is done; no more reads
        timeseries = D.roi_means(np.zeros((16 * 16, 0)))
    assert timeseries.shape == (0, 37)


def test_batches_smaller_than_one_frame_are_2_frames_wide(tmp_path, monkeypatch):
    """Frames over half of BATCH_BYTES (above 2048 x 2048 at 64 MiB)."""
    monkeypatch.setattr(lccd_detection, "BATCH_BYTES", 1)
    path = write(tmp_path / "m.tif", 37, np.int16)
    expected_movie = whole_movie(path)
    roi = random_rois(expected_movie.shape, 12, np.random.default_rng(1))
    with LoadData(ImageData([path])) as D:
        actual = D.roi_means(roi)
    assert actual.tobytes() == whole_movie_means(expected_movie, roi).tobytes()


def test_non_square_frames_fail_as_before(tmp_path):
    path = write(tmp_path / "m.tif", 5, np.uint16, shape=(16, 12))
    with pytest.raises(ValueError):
        whole_movie(path)
    with pytest.raises(ValueError):
        LoadData(ImageData([path]))


@pytest.fixture
def opened(monkeypatch):
    """The file object of every PIL image opened (Pillow sets the image's own
    fp to None once a page is loaded, so the image cannot tell)."""
    files = []
    real_open = Image.open

    def recording_open(*args, **kwargs):
        image = real_open(*args, **kwargs)
        files.append(image.fp)
        return image

    monkeypatch.setattr(Image, "open", recording_open)
    return files


def test_the_file_is_closed_after_the_block(tmp_path, opened):
    path = write(tmp_path / "m.tif", 37, np.uint16)
    with LoadData(ImageData([path])):
        pass
    assert [f.closed for f in opened] == [True]


def test_the_file_is_closed_when_the_scan_fails(tmp_path, opened):
    path = write(tmp_path / "m.tif", 5, np.uint16, shape=(16, 12))
    with pytest.raises(ValueError):
        LoadData(ImageData([path]))
    assert [f.closed for f in opened] == [True]


@pytest.fixture
def lccd_stub(monkeypatch):
    """LCCD's own window loop, recording what it reads, then fixed ROIs."""
    seen = []

    class FakeLCCD:
        def __init__(self, params):
            pass

        def apply(self, v):
            X, Y, T = v.shape
            for m in range(0, T // FRAME_DIVIDER - 1):
                window = v[:, :, m * FRAME_DIVIDER : (m + 1) * FRAME_DIVIDER]
                seen.append((m * FRAME_DIVIDER, window.tobytes()))
            return random_rois((X, Y), 5, np.random.default_rng(2))

    fake = types.ModuleType("lccd")
    fake.LCCD = FakeLCCD
    monkeypatch.setitem(
        sys.modules, f"{lccd_detection.__package__}.lccd_python.lccd", fake
    )
    return seen


def test_lccd_detect_matches_the_whole_movie(tmp_path, small_batches, lccd_stub):
    path = write(tmp_path / "mc_images.tif", 37, np.int16)
    output_dir = tmp_path / "output" / "default" / "uid" / "lccd"
    output_dir.mkdir(parents=True)
    info = lccd_detect(
        ImageData([path]), str(output_dir), params={"f0_frames": 5, "f0_percentile": 8}
    )

    D = whole_movie(path)
    assert [start for start, _ in lccd_stub] == [0, 10]
    for start, window in lccd_stub:
        assert window == D[:, :, start : start + FRAME_DIVIDER].tobytes()
    roi = random_rois(D.shape[:2], 5, np.random.default_rng(2))
    assert info["fluorescence"].data.tobytes() == whole_movie_means(D, roi).tobytes()


def test_the_file_is_closed_when_lccd_raises(tmp_path, opened, monkeypatch, lccd_stub):
    def fail(self, v):
        raise MemoryError

    lccd = sys.modules[f"{lccd_detection.__package__}.lccd_python.lccd"]  # the stub
    monkeypatch.setattr(lccd.LCCD, "apply", fail)
    path = write(tmp_path / "m.tif", 37, np.int16)
    output_dir = tmp_path / "output" / "default" / "uid" / "lccd"
    output_dir.mkdir(parents=True)
    with pytest.raises(MemoryError):
        lccd_detect(ImageData([path]), str(output_dir), params={})
    assert [f.closed for f in opened] == [True]


def test_vacant_roi_decodes_no_frame(tmp_path, monkeypatch):
    path = write(tmp_path / "m.tif", 37, np.uint16)

    def no_decode(self):
        raise AssertionError("a frame was decoded")

    monkeypatch.setattr(TiffImagePlugin.TiffImageFile, "load", no_decode)
    assert movie_shape(ImageData([path])) == (16, 16, 37)


def test_vacant_roi_outputs_follow_the_frame_orientation(tmp_path):
    path = write(tmp_path / "m.tif", 7, np.uint16, shape=(16, 12))
    output_dir = tmp_path / "output" / "default" / "uid" / "vacant"
    output_dir.mkdir(parents=True)
    info = vacant_roi(ImageData([path]), str(output_dir), params={})

    roi_masks = info["edit_roi_data"].roi_masks
    assert len(roi_masks) == 0 and roi_masks.shape == (16, 12)
    assert info["all_roi"].data.shape == (16, 12)
