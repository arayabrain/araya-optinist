"""
Shared utility functions for Optinist wrappers.
"""

import numpy as np

__all__ = [
    "standard_norm",
    "recursive_flatten_params",
    "param_check",
    "peak_order",
]


def standard_norm(X, mean, std):
    from sklearn.preprocessing import StandardScaler

    sc = StandardScaler(with_mean=mean, with_std=std)
    tX = sc.fit_transform(X)
    return tX


def recursive_flatten_params(params, result_params: dict, nest_counter=0):
    assert nest_counter <= 10, f"Nest depth overflow. [{nest_counter}]"
    nest_counter += 1

    for key, nested_param in params.items():
        if type(nested_param) is dict:
            recursive_flatten_params(nested_param, result_params, nest_counter)
        else:
            result_params[key] = nested_param


def param_check(params):
    for key in params:
        if (params[key] == "") or (params[key] == "None"):
            params[key] = None
    return params


def peak_order(norm_mean):
    """Row order by each row's peak column, earliest first; ties keep row order.

    NaN never wins the argmax, so an empty bin does not become a row's peak and
    an all-NaN row sorts first, with the flat rows.
    """
    peak = np.argmax(np.nan_to_num(norm_mean, nan=-1.0), axis=1)
    return np.argsort(peak, kind="stable")
