"""
Utilities for resolving Snakemake configuration file paths in batch environments.

This module provides a function to determine the correct path for the Snakemake
configuration YAML file, primarily for use within AWS Batch or similar
containerized execution contexts.
"""
import os


def get_batch_config_path() -> str:
    """
    Get config path for batch mode execution.
    Tries primary location first, then fallback.
    """
    # Try primary location first
    if os.path.exists("/app/snakemake.yaml"):
        return "/app/snakemake.yaml"
    # Fallback if deploy-sources overwrote /app version
    elif os.path.exists("/tmp/snakemake_config.yaml"):
        return "/tmp/snakemake_config.yaml"
    else:
        # Return expected path for clear error
        return "/app/snakemake.yaml"
