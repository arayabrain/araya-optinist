from studio.app.optinist.wrappers.optinist.basic_neural_analysis.condition_split import (  # noqa: E501
    condition_split,
)
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.covariate_binning import (  # noqa: E501
    covariate_binning,
)
from studio.app.optinist.wrappers.optinist.basic_neural_analysis.eta import ETA

basic_neural_analysis_wrapper_dict = {
    "eta": {"function": ETA},
    "covariate_binning": {"function": covariate_binning},
    "condition_split": {"function": condition_split},
}
