import numpy as np

from studio.app.optinist.wrappers.caiman import cnmf


def caiman_local_correlations(Y):
    """caiman 1.11.4 local_correlations, 8 neighbours, on a whole (T, d1, d2) movie."""
    w = (Y - np.mean(Y, axis=0)) / np.std(Y, axis=0)
    rho_h = np.mean(w[:, :-1, :] * w[:, 1:, :], axis=0)
    rho_w = np.mean(w[:, :, :-1] * w[:, :, 1:], axis=0)
    rho_d1 = np.mean(w[:, 1:, :-1] * w[:, :-1, 1:], axis=0)
    rho_d2 = np.mean(w[:, :-1, :-1] * w[:, 1:, 1:], axis=0)
    rho = np.zeros(Y.shape[1:])
    rho[:-1, :] = rho[:-1, :] + rho_h
    rho[1:, :] = rho[1:, :] + rho_h
    rho[:, :-1] = rho[:, :-1] + rho_w
    rho[:, 1:] = rho[:, 1:] + rho_w
    rho[:-1, :-1] = rho[:-1, :-1] + rho_d2
    rho[1:, 1:] = rho[1:, 1:] + rho_d1
    rho[1:, :-1] = rho[1:, :-1] + rho_d1
    rho[:-1, 1:] = rho[:-1, 1:] + rho_d2
    neighbors = 8 * np.ones(Y.shape[1:])
    neighbors[0, :] = neighbors[0, :] - 3
    neighbors[-1, :] = neighbors[-1, :] - 3
    neighbors[:, 0] = neighbors[:, 0] - 3
    neighbors[:, -1] = neighbors[:, -1] - 3
    neighbors[0, 0] = neighbors[0, 0] + 1
    neighbors[-1, -1] = neighbors[-1, -1] + 1
    neighbors[-1, 0] = neighbors[-1, 0] + 1
    neighbors[0, -1] = neighbors[0, -1] + 1
    return rho / neighbors


def test_chunked_correlation_image_equals_caiman_on_the_whole_movie(monkeypatch):
    # 8-frame chunks over 50 frames: six full chunks and a ragged one
    monkeypatch.setattr(cnmf, "CORRELATION_CHUNK_ELEMENTS", 6 * 7 * 8)
    rng = np.random.default_rng(0)
    movie = rng.integers(0, 200, (50, 6, 7)).astype(np.float32)
    movie[:, :3, :3] += rng.integers(0, 50, (50, 1, 1))  # a correlated cell
    movie[:, 5, 6] = 9  # a constant pixel has no correlation: NaN, as in caiman

    with np.errstate(invalid="ignore"):
        expected = caiman_local_correlations(movie.astype(np.float64))
        actual, mean = cnmf.correlation_image(movie)

    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    np.testing.assert_allclose(mean, movie.astype(np.float64).mean(axis=0), rtol=1e-12)
    assert np.isnan(actual[5, 6])
