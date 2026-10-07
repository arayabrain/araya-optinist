from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.core.utils.filepath_finder import find_param_filepath

logger = AppLogger.get_logger()


def read_default_params(name: str):
    filepath = find_param_filepath(name)
    return ConfigReader.read(filepath)


FAQ_URL = "https://github.com/oist/optinist/wiki/FAQ"


def get_typecheck_params(message_params, name):
    default_params = read_default_params(name)
    if not isinstance(default_params, dict):
        default_params = {}
    if message_params != {} and message_params is not None:
        params = nest2dict(message_params)
        if not default_params:
            # No yaml to check against (a plugin algorithm); snakemake reports
            # the missing rule itself
            return params
        if has_outdated_shape(params, default_params):
            unknown = ", ".join(
                f"'{k}'" for k in sorted(params.keys() - default_params)
            )
            logger.warning(
                f"Invalid Workflow yaml params: {unknown} in [{name}]. See {FAQ_URL}"
            )
            raise KeyError(
                f"Workflow yaml error, see FAQ: unknown parameters {unknown} for "
                f"{name or 'this node'}; reset the node's parameters and run again"
            )
        return check_types(params, default_params, name)
    return default_params


def has_outdated_shape(params, default_params):
    """
    True when the saved tree looks like it was written against a different
    yaml layout (e.g. OptiNiSt v1): it has unknown top-level keys and either
    shares no key with the defaults or has a key that today lives inside one
    of the default groups (v2 nested the flat v1 keys into groups).
    """
    if not params:
        return False
    unknown = params.keys() - default_params.keys()
    if not unknown:
        return False
    no_overlap = len(unknown) == len(params)
    return no_overlap or bool(unknown & _nested_keys(default_params))


def _nested_keys(default_params) -> set:
    nested = set()
    for value in default_params.values():
        if isinstance(value, dict):
            nested |= value.keys() | _nested_keys(value)
    return nested


def check_types(params, default_params, name=""):
    for key in list(params):
        if key not in default_params:
            logger.warning(
                f"Dropping saved param '{key}' for [{name}]: "
                f"not in the current default yaml. See {FAQ_URL}"
            )
            del params[key]
        elif isinstance(params[key], dict) != isinstance(default_params[key], dict):
            logger.warning(
                f"Dropping saved param '{key}' for [{name}]: "
                f"its shape differs from the current default yaml. See {FAQ_URL}"
            )
            del params[key]
        elif isinstance(params[key], dict):
            params[key] = check_types(params[key], default_params[key], name)
        else:
            if not isinstance(type(params[key]), type(default_params[key])):
                data_type = type(default_params[key])
                p = params[key]
                if isinstance(data_type, str):
                    params[key] = str(p)
                elif isinstance(data_type, float):
                    params[key] = float(p)
                elif isinstance(data_type, int):
                    params[key] = int(p)

    return params


def nest2dict(value):
    nwb_dict = {}
    for _k, _v in value.items():
        if _v["type"] == "child":
            nwb_dict[_k] = _v["value"]
        elif _v["type"] == "parent":
            nwb_dict[_k] = nest2dict(_v["children"])

    return nwb_dict
