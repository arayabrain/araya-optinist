import os
from typing import List

import h5py
from fastapi import APIRouter, Depends

from studio.app.common.core.auth.auth_dependencies import get_user_remote_bucket_name
from studio.app.common.core.logger import AppLogger
from studio.app.common.core.utils.filepath_creater import join_filepath
from studio.app.common.core.workspace.workspace_dependencies import (
    is_workspace_available,
)
from studio.app.common.dataclass.utils import is_plain_number
from studio.app.common.routers.files import (
    HDF5_STRUCTURE_VERSION,
    STRUCTURE_VERSION_KEY,
    download_structure_cache,
    get_hdf5_structure_dict,
)
from studio.app.const import MetadataCacheFile
from studio.app.dir_path import DIRPATH
from studio.app.optinist.schemas.hdf5 import HDF5Node

router = APIRouter()

logger = AppLogger.get_logger()


class HDF5Getter:
    @classmethod
    def get(cls, filepath) -> List[HDF5Node]:
        cls.hdf5_list: List[HDF5Node] = []
        with h5py.File(filepath, "r") as f:
            f.visititems(cls.get_ds_dictionaries)

        return cls.hdf5_list

    @classmethod
    def get_ds_dictionaries(cls, path: str, node: h5py.Dataset):
        if isinstance(node, h5py.Dataset):
            if node.shape:  # skips scalar and null-dataspace datasets
                cls.recursive_dir_tree(cls.hdf5_list, path.split("/"), node, "")

    @classmethod
    def recursive_dir_tree(
        cls,
        node_list: List[HDF5Node],
        path_list: List[str],
        node: h5py.Dataset,
        parent_path: str,
    ):
        name = path_list[0]
        if name.startswith("#"):
            return

        path = name if parent_path == "" else f"{parent_path}/{name}"

        is_exists = False
        # 既にkeyがある
        for i, value in enumerate(node_list):
            if value.name == name:
                is_exists = True
                if len(path_list) > 1:
                    cls.recursive_dir_tree(
                        node_list[i].nodes, path_list[1:], node, path
                    )

        if not is_exists:
            if len(path_list) > 1:
                node_list.append(
                    HDF5Node(
                        isDir=True,
                        name=name,
                        path=path,
                        nodes=[],
                    )
                )
                cls.recursive_dir_tree(node_list[-1].nodes, path_list[1:], node, path)
            elif node.dtype.names and node.ndim == 1:
                fields = cls.compound_fields(node, path)
                if fields:
                    node_list.append(
                        HDF5Node(isDir=True, name=name, path=path, nodes=fields)
                    )
            else:
                node_list.append(
                    HDF5Node(
                        isDir=False,
                        name=name,
                        path=path,
                        shape=node.shape,
                        nbytes=node.nbytes,
                        dataType="array",
                    )
                )

    @staticmethod
    def compound_fields(node: h5py.Dataset, path: str) -> List[HDF5Node]:
        """One leaf per numeric column of a table-like dataset, loadable as 1D."""
        return [
            HDF5Node(
                isDir=False,
                name=field,
                path=f"{path}/{field}",
                shape=node.shape,
                nbytes=node.dtype[field].itemsize * node.size,
                dataType="array",
            )
            for field in node.dtype.names
            if "/" not in field and is_plain_number(node.dtype[field])
        ]


def _dict_to_hdf5_node(d: dict) -> HDF5Node:
    """Convert a dict from cached JSON back to HDF5Node."""
    return HDF5Node(
        isDir=d["isDir"],
        name=d["name"],
        path=d["path"],
        nodes=(
            [_dict_to_hdf5_node(n) for n in d.get("nodes", [])] if d["isDir"] else None
        ),
        shape=tuple(d["shape"]) if d.get("shape") else None,
        nbytes=d.get("nbytes"),
        dataType=d.get("dataType"),
    )


@router.get(
    "/hdf5/{file_path:path}",
    response_model=List[HDF5Node],
    dependencies=[Depends(is_workspace_available)],
    tags=["outputs"],
)
async def get_files(
    file_path: str,
    workspace_id: str,
    remote_bucket_name: str = Depends(get_user_remote_bucket_name),
):
    """Get HDF5 file structure.

    First checks for cached structure in .hdf5_structure.json (downloaded from S3
    if remote storage is available). Falls back to extracting from the file directly.
    """
    # Try to download cached structure from S3 first
    await download_structure_cache(
        remote_bucket_name, workspace_id, MetadataCacheFile.HDF5_STRUCTURE
    )

    structure_dict = get_hdf5_structure_dict(workspace_id)
    full_path = join_filepath([DIRPATH.INPUT_DIR, workspace_id, file_path])
    versions = structure_dict.get(STRUCTURE_VERSION_KEY)
    is_current = (
        isinstance(versions, dict) and versions.get(file_path) == HDF5_STRUCTURE_VERSION
    )
    # An older entry lists a compound dataset as one leaf, so prefer the file
    if file_path in structure_dict and (is_current or not os.path.exists(full_path)):
        return [_dict_to_hdf5_node(node) for node in structure_dict[file_path]]

    # Fall back to extracting from file directly
    try:
        return HDF5Getter.get(full_path)
    except Exception as e:
        if file_path not in structure_dict:
            raise
        logger.warning(f"Failed to read HDF5 structure for {file_path}: {e}")
        return [_dict_to_hdf5_node(node) for node in structure_dict[file_path]]
