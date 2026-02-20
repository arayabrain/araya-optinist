"""RC3: EFS mount path regression tests for compute.tf.

Ensures the snakemake volume containerPath is correct and
consistent across all ECS task definitions.
"""

import re
from pathlib import Path

import pytest

# Locate compute.tf relative to the project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
COMPUTE_TF = PROJECT_ROOT / "infrastructure" / "terraform" / "compute.tf"

EXPECTED_MOUNT_PATH = "/app/.snakemake"

# Matches mountPoints blocks that reference a snakemake volume
_MOUNT_BLOCK_RE = re.compile(
    r"mountPoints\s*=\s*\[\s*\{[^}]*?"
    r"sourceVolume\s*=\s*\"[^\"]*snmk-volume\"[^}]*?"
    r"containerPath\s*=\s*\"([^\"]+)\"",
    re.DOTALL,
)


class TestEFSMountPaths:
    """RC3: Verify EFS mount paths in compute.tf."""

    @pytest.fixture(autouse=True)
    def _load_tf(self):
        assert COMPUTE_TF.exists(), f"compute.tf not found at {COMPUTE_TF}"
        self.tf_content = COMPUTE_TF.read_text()

    def test_premium_mount_path(self):
        """Premium task definition must mount snakemake
        volume at /app/.snakemake (was /efs before fix)."""
        # Find premium task definition section
        premium_section = re.search(
            r'resource\s+"aws_ecs_task_definition"\s+'
            r'"premium".*?(?=resource\s+"aws_)',
            self.tf_content,
            re.DOTALL,
        )
        assert premium_section, "Premium task definition not found in compute.tf"
        match = _MOUNT_BLOCK_RE.search(premium_section.group())
        assert match, "snakemake mountPoints not found in premium " "task definition"
        assert match.group(1) == EXPECTED_MOUNT_PATH

    def test_asg_and_premium_mount_paths_match(self):
        """All snakemake volume containerPaths must be
        identical across task definitions."""
        paths = _MOUNT_BLOCK_RE.findall(self.tf_content)
        assert len(paths) >= 2, (
            f"Expected at least 2 snakemake mount blocks, " f"found {len(paths)}"
        )
        assert all(
            p == EXPECTED_MOUNT_PATH for p in paths
        ), f"Mount path mismatch: {paths}"
