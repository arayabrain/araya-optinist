import os
from dataclasses import dataclass
from typing import Dict, Optional

from studio.app.common.core.snakemake.smk import SmkParam
from studio.app.common.core.workflow.workflow import OutputPath
from studio.app.dir_path import DIRPATH
from studio.app.optinist.schemas.nwb import NWBParams


@dataclass
class ExptFunction:
    unique_id: str
    name: str
    success: str
    hasNWB: bool
    message: Optional[str] = None
    outputPaths: Optional[Dict[str, OutputPath]] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


@dataclass
class ExptConfig:
    workspace_id: str
    unique_id: str
    name: str
    started_at: str
    finished_at: Optional[str]
    success: Optional[str]
    hasNWB: bool
    function: Dict[str, ExptFunction]
    procs: Optional[Dict[str, ExptFunction]]
    nwb: NWBParams
    snakemake: SmkParam
    data_usage: Optional[int]
    timezone: Optional[str] = None  # User's browser timezone (IANA format)

    @staticmethod
    def required_fields():
        return [
            "workspace_id",
            "unique_id",
            "name",
            "started_at",
            "hasNWB",
            "function",
            "nwb",
            "snakemake",
        ]


@dataclass
class ExptExtConfig(ExptConfig):
    is_remote_synced: Optional[bool] = None


@dataclass
class ExptOutputPathIds:
    output_dir: Optional[str] = None
    workspace_id: Optional[str] = None
    unique_id: Optional[str] = None
    function_id: Optional[str] = None

    def __post_init__(self):
        """
        Extract each ID from output_path
        - output_dir format (absolute or relative)
          - {DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/{function_id}
          - {workspace_id}/{unique_id}/{function_id}
        """
        if self.output_dir:
            path = self.output_dir.replace("\\", "/")

            # Handle both absolute and relative paths
            if path.startswith(DIRPATH.OUTPUT_DIR.replace("\\", "/")):
                # Absolute path - extract relative portion
                output_relative_dir = os.path.relpath(
                    path, DIRPATH.OUTPUT_DIR.replace("\\", "/")
                ).replace("\\", "/")
            else:
                # Already relative
                output_relative_dir = path

            splitted_ids = output_relative_dir.split("/")
        else:
            output_relative_dir = None
            splitted_ids = []

        ids_count = len(splitted_ids)

        if ids_count >= 3:
            self.workspace_id, self.unique_id, self.function_id = splitted_ids[:3]
        elif ids_count == 2:
            self.workspace_id, self.unique_id = splitted_ids
        else:
            assert False, (
                "Invalid path specified: "
                f"[ids_count: {ids_count}] [path: {output_relative_dir}]"
            )

    @classmethod
    def from_request_url(
        cls, request_url_path: str, outputs_url_prefix: str = r"^/outputs/[^/]+/"
    ) -> "ExptOutputPathIds":
        """
        Extract workspace_id and unique_id from a request URL path.

        Handles patterns:
        - /outputs/image//app/studio_data/output/{workspace_id}/{unique_id}/...
        - /outputs/thumbnail/{workspace_id}/{unique_id}/...

        Args:
            request_url_path: The URL path from the request
            outputs_url_prefix: Regex pattern to strip from path
                (default: r"^/outputs/[^/]+/")

        Returns:
            ExptOutputPathIds with workspace_id and unique_id
            (or empty instance if parsing fails)
        """
        import re

        from studio.app.common.core.storage.remote_storage_controller import (
            RemoteStorageType,
        )
        from studio.app.common.core.storage.s3_storage_controller import (
            S3StorageController,
        )

        data_file_path = re.sub(outputs_url_prefix, "", request_url_path)

        # Handle absolute paths starting with DIRPATH.OUTPUT_DIR
        # or the production S3 path (used in URLs)
        output_path_prefixes = [DIRPATH.OUTPUT_DIR]
        if RemoteStorageType.get_activated_type() == RemoteStorageType.S3:
            s3_output_path = "/" + S3StorageController.make_s3_output_prefix().rstrip(
                "/"
            )
            output_path_prefixes.append(s3_output_path)

        for prefix in output_path_prefixes:
            if data_file_path.startswith(prefix):
                relative_path = data_file_path[len(prefix) :].lstrip("/")
                path_parts = relative_path.split("/")
                if len(path_parts) >= 2:
                    try:
                        return cls(output_dir="/".join(path_parts[:2]))
                    except (ValueError, IndexError, AssertionError):
                        pass

        # Handle simpler relative paths: {workspace_id}/{unique_id}/...
        path_parts = data_file_path.split("/")
        if len(path_parts) >= 2:
            potential_workspace_id = path_parts[0]
            if potential_workspace_id.isdigit():
                return cls(output_dir="/".join(path_parts[:2]))

        # Return instance with None values if parsing fails
        instance = cls.__new__(cls)
        instance.output_dir = None
        instance.workspace_id = None
        instance.unique_id = None
        instance.function_id = None
        return instance
