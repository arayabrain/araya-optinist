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
        - output_dir format
          - {DIRPATH.OUTPUT_DIR}/{workspace_id}/{unique_id}/{function_id}

        In AWS Batch with S3 storage, paths look like:
          - .snakemake/storage/s3/{bucket}/app/studio_data/...
          - output/{workspace_id}/{unique_id}/{function_id}
        """
        if not self.output_dir:
            return

        output_dir_normalized = self.output_dir.replace("\\", "/")

        # In AWS Batch with S3 storage, extract IDs from the S3 path structure
        # Path format: .snakemake/storage/s3/{bucket}/...
        # app/studio_data/output/{workspace_id}/{unique_id}/{function_id}
        is_batch_s3 = (
            os.environ.get("IN_SNAKEMAKE_BATCH") == "true"
            and ".snakemake/storage/s3/" in output_dir_normalized
        )

        if is_batch_s3:
            # Find the "output/" segment and extract everything after it
            output_marker = "/output/"
            if output_marker in output_dir_normalized:
                # "/output/" is: {workspace_id}/{unique_id}/{function_id}
                ids_part = output_dir_normalized.split(output_marker, 1)[1]
                splitted_ids = ids_part.rstrip("/").split("/")
            else:
                # Fallback: couldn't find expected structure
                splitted_ids = []
        else:
            # Local/EFS mode: compute relative path from DIRPATH.OUTPUT_DIR
            output_relative_dir = os.path.relpath(
                output_dir_normalized,
                DIRPATH.OUTPUT_DIR.replace("\\", "/"),
            ).replace("\\", "/")
            splitted_ids = output_relative_dir.split("/")

        ids_count = len(splitted_ids)

        if ids_count == 3:
            self.workspace_id, self.unique_id, self.function_id = splitted_ids
        elif ids_count == 2:
            self.workspace_id, self.unique_id = splitted_ids
        else:
            assert False, (
                "Invalid path specified: "
                f"[ids_count: {ids_count}] [path: {output_dir_normalized}]"
            )
