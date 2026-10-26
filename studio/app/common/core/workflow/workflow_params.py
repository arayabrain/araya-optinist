from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.config_handler import ConfigReader
from studio.app.common.core.utils.filepath_finder import find_param_filepath

logger = AppLogger.get_logger()


def read_default_params(name: str):
    filepath = find_param_filepath(name)
    return ConfigReader.read(filepath)


FAQ_URL = "https://github.com/oist/optinist/wiki/FAQ"


def get_typecheck_params(message_params, name, added=None):
    """The saved params checked against the node's yaml; added paths go to `added`."""
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
        return check_types(params, default_params, name, added=added)
    return default_params


def has_outdated_shape(params, default_params):
    """True for a saved tree from another yaml layout: refused, not filled."""
    if not params:
        return False
    unknown = params.keys() - default_params.keys()
    if not unknown:
        return False
    # nothing shared, a flat key now in a group (v1), or a group replaced by another
    no_overlap = len(unknown) == len(params)
    moved_key = bool(unknown & _nested_keys(default_params))
    replaced_group = any(isinstance(params[key], dict) for key in unknown) and any(
        isinstance(value, dict) and key not in params
        for key, value in default_params.items()
    )
    return no_overlap or moved_key or replaced_group


def _nested_keys(default_params) -> set:
    nested = set()
    for value in default_params.values():
        if isinstance(value, dict):
            nested |= value.keys() | _nested_keys(value)
    return nested


def check_types(params, default_params, name="", prefix="", added=None):
    """Drop keys the yaml lost, add the ones it gained, cast types; in yaml order."""
    for key in list(params):
        path = f"{prefix}{key}"
        if key not in default_params:
            logger.warning(
                f"Dropping saved param '{path}' for [{name}]: "
                f"not in the current default yaml. See {FAQ_URL}"
            )
            del params[key]
        elif isinstance(params[key], dict) != isinstance(default_params[key], dict):
            logger.warning(
                f"Dropping saved param '{path}' for [{name}]: "
                f"its shape differs from the current default yaml. See {FAQ_URL}"
            )
            del params[key]
        elif isinstance(params[key], dict):
            params[key] = check_types(
                params[key], default_params[key], name, f"{path}/", added
            )
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

    for key, value in default_params.items():
        if key not in params:
            logger.warning(
                f"Adding param '{prefix}{key}' for [{name}]: not in the saved "
                f"params, using the current default yaml value {value!r}"
            )
            params[key] = value
            if added is not None:
                added.append(f"{prefix}{key}")

    return {key: params[key] for key in default_params}


def nest2dict(value):
    nwb_dict = {}
    for _k, _v in value.items():
        if _v["type"] == "child":
            nwb_dict[_k] = _v["value"]
        elif _v["type"] == "parent":
            nwb_dict[_k] = nest2dict(_v["children"])

    return nwb_dict


def dict2nest(params, prefix=""):
    """The parameter form's tree of plain params: the inverse of nest2dict."""
    return {
        key: (
            {"type": "parent", "children": dict2nest(value, f"{prefix}{key}/")}
            if isinstance(value, dict)
            else {"type": "child", "value": value, "path": f"{prefix}{key}"}
        )
        for key, value in params.items()
    }
