import itertools
import sys
import types
import weakref

import numpy as np
import pytest

from studio.app.optinist.core.nwb.nwb import NWBDATASET
from studio.app.optinist.dataclass import FluoData
from studio.app.optinist.wrappers.optinist.neural_population_analysis import granger

N_TIME, N_CELL, MAXLAG = 40, 4, 2
PAIRS = list(itertools.permutations(range(N_CELL), 2))
PARAMS = {
    "I/O": {"transpose": False, "standard_mean": True, "standard_std": True},
    "Granger": {
        "Granger_maxlag": MAXLAG,
        "Granger_addconst": True,
        "use_adfuller_test": True,
        "use_coint_test": True,
        "adfuller": {},
        "coint": {},
    },
}


class FittedModel:
    """Stands in for a statsmodels OLS result, which holds its design matrices."""


@pytest.fixture
def output_dir(tmp_path):
    d = tmp_path / "output" / "default" / "uid" / "func"
    d.mkdir(parents=True)
    return str(d)


@pytest.fixture
def stattools(monkeypatch):
    """A fake statsmodels.tsa.stattools: test values follow the call order, and
    each Granger call checks that the models returned two calls ago are freed
    (the wrapper's own `tp` still holds the previous call's)."""
    calls = []

    def grangercausalitytests(x, maxlag, verbose, addconst):
        for earlier in calls[:-1]:
            assert all(ref() is None for ref in earlier), "fitted models kept"
        n = len(calls)
        models = [FittedModel() for _ in range(2 * maxlag)]
        calls.append([weakref.ref(m) for m in models])
        return {
            lag: (
                {
                    "ssr_ftest": (10.0 * n + lag, 0.5, 30.0, lag),
                    "ssr_chi2test": (n + 0.1, 0.4, lag),
                    "lrtest": (n + 0.2, 0.3, lag),
                    "params_ftest": (n + 0.3, 0.2, 30.0, lag),
                },
                [models[2 * lag - 2], models[2 * lag - 1], np.eye(lag, 2 * lag + 1)],
            )
            for lag in range(1, maxlag + 1)
        }

    module = types.ModuleType("statsmodels.tsa.stattools")
    module.grangercausalitytests = grangercausalitytests
    module.adfuller = lambda x, **kw: (-3.0, 0.01, 1, N_TIME - 2, {"1%": -3.5}, 1.0)
    module.coint = lambda a, b, **kw: (-2.0, 0.04, np.array([-3.9, -3.3, -3.0]))
    for name in ("statsmodels", "statsmodels.tsa"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "statsmodels.tsa.stattools", module)
    # the wrapper's scikit-learn scaler, which the test env does not install
    monkeypatch.setattr(
        granger, "standard_norm", lambda X, mean, std: (X - X.mean(0)) / X.std(0)
    )
    return calls


def test_granger_keeps_no_fitted_model_per_pair(output_dir, stattools):
    X = np.random.default_rng(0).standard_normal((N_TIME, N_CELL))

    info = granger.Granger(FluoData(X), output_dir, params=PARAMS)

    assert len(stattools) == len(PAIRS)
    (post,) = info["nwbfile"][NWBDATASET.POSTPROCESS].values()
    for n, (a, b) in enumerate(PAIRS):
        for lag in range(1, MAXLAG + 1):
            np.testing.assert_array_equal(
                post["gc_ssr_ftest"][n, lag - 1], [10.0 * n + lag, 0.5, 30.0, lag]
            )
            np.testing.assert_array_equal(
                post["gc_lrtest"][n, lag - 1], [n + 0.2, 0.3, lag]
            )
        assert post["Granger_fval_mat"][a, b] == 10.0 * n + 1
    assert np.all(np.diag(post["Granger_fval_mat"]) == 0)
    np.testing.assert_array_equal(
        info["Granger_fval_mat_heatmap"].data, post["Granger_fval_mat"]
    )
