import inspect
import os
from typing import Dict, Optional

import h5py
import numpy as np

from studio.app.common.core.storage.remote_storage_controller import (
    RemoteStorageController,
    RemoteStorageSimpleReader,
)
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workflow.workflow import Edge, Node, NodeType
from studio.app.common.dataclass.base import BaseData
from studio.app.common.dataclass.image import ImageData
from studio.app.common.routers.files import (
    get_hdf5_structure_dict,
    get_mat_structure_dict,
)
from studio.app.const import NOT_DISPLAY_ARGS_LIST, MetadataCacheFile
from studio.app.dir_path import DIRPATH
from studio.app.optinist.routers.mat import MatGetter
from studio.app.wrappers import wrapper_dict


class WorkflowValidationError(ValueError):
    pass


async def ensure_structure_caches(remote_bucket_name: str, workspace_id: str):
    if not RemoteStorageController.is_available():
        return
    for cache_file in (
        MetadataCacheFile.HDF5_STRUCTURE,
        MetadataCacheFile.MAT_STRUCTURE,
    ):
        try:
            async with RemoteStorageSimpleReader(remote_bucket_name) as reader:
                await reader.download_input_data(workspace_id, cache_file)
        except Exception:
            pass


def validate_input_edges(
    workspace_id: str, nodeDict: Dict[str, Node], edgeDict: Dict[str, Edge]
):
    """Reject HDF5/Matlab datasets wired into an input of an incompatible kind.

    Only rank is checked: 3D+ datasets fit ImageData only, 1D/2D fit anything else.
    Anything that cannot be resolved (missing cache and file, unknown function,
    untyped arg) is skipped; the node error path still catches it at run time.
    """
    for edge in edgeDict.values():
        source = nodeDict.get(edge.source)
        target = nodeDict.get(edge.target)
        if source is None or target is None:
            continue
        if source.type not in (NodeType.HDF5, NodeType.MATLAB):
            continue
        if not isinstance(target.data.path, str) or target.data.path.startswith(
            "maintenance/"
        ):
            continue

        arg_name = edge.targetHandle.split("--")[1]
        expected = _expected_arg_type(target.data.path, arg_name)
        if expected is None:
            continue

        dataset_path = (
            source.data.hdf5Path
            if source.type == NodeType.HDF5
            else source.data.matPath
        )
        shape = _dataset_shape(workspace_id, source, dataset_path)
        if shape is None:
            continue

        expects_image = issubclass(expected, ImageData)
        if (len(shape) >= 3) != expects_image:
            raise WorkflowValidationError(
                f"{source.type} dataset '{dataset_path}' has shape {tuple(shape)} "
                f"but {target.data.label}.{arg_name} expects {expected.__name__} "
                f"({'3D' if expects_image else '1D or 2D'})"
            )


def _expected_arg_type(function_path: str, arg_name: str) -> Optional[type]:
    entry = wrapper_dict
    for key in function_path.split("/"):
        if not isinstance(entry, dict) or key not in entry:
            return None
        entry = entry[key]
    if not isinstance(entry, dict) or "function" not in entry:
        return None
    if arg_name in NOT_DISPLAY_ARGS_LIST:
        return None
    param = inspect.signature(entry["function"]).parameters.get(arg_name)
    if param is None or not inspect.isclass(param.annotation):
        return None
    if param.annotation is BaseData or not issubclass(param.annotation, BaseData):
        return None
    return param.annotation


def _dataset_shape(workspace_id: str, source: Node, dataset_path: str):
    if not dataset_path:
        return None
    file_path = source.data.path
    if isinstance(file_path, list):
        file_path = file_path[0]

    cache = (
        get_hdf5_structure_dict(workspace_id)
        if source.type == NodeType.HDF5
        else get_mat_structure_dict(workspace_id)
    )
    shape = _find_shape(cache.get(file_path, []), dataset_path)
    if shape is not None:
        return shape

    workspace_dir = os.path.realpath(join_filepath([DIRPATH.INPUT_DIR, workspace_id]))
    local_path = os.path.realpath(join_filepath([workspace_dir, file_path]))
    if not local_path.startswith(workspace_dir + os.sep) or not os.path.isfile(
        local_path
    ):
        return None
    try:
        if source.type == NodeType.HDF5:
            with h5py.File(local_path, "r") as f:
                return f[dataset_path].shape
        return np.shape(MatGetter.data(local_path, dataset_path))
    except Exception:
        return None


def _find_shape(nodes: list, dataset_path: str):
    for node in nodes:
        if node.get("path") == dataset_path and node.get("shape") is not None:
            return node["shape"]
        found = _find_shape(node.get("nodes", []), dataset_path)
        if found is not None:
            return found
    return None
