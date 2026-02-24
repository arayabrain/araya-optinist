"""
TimeSeriesChunkHandler: Centralized chunk management for timeseries/csv data.

This module provides a unified interface for saving and loading timeseries data
in chunked format, reducing the number of JSON files generated while maintaining
compatibility with legacy single-file-per-record format.

Design:
- Writer: Groups multiple records into chunk files (chunk_0.json, chunk_1.json, ...)
- Reader: Loads data from chunks with transparent format detection
- Index mapping: Maintains record_id -> chunk_id mapping for efficient lookup

Backward Compatibility:
- Chunk size changes do NOT break existing data
- Each chunk file and chunk_index_map.json are self-contained
- Reading relies on chunk_index_map.json, not on CHUNK_SIZE constant
- Old data (e.g., 100 records/chunk) coexists with new data (e.g., 200 records/chunk)
"""

import json
import math
import os
from glob import glob
from typing import Any, Dict, List

import pandas as pd

from studio.app.common.core.utils.filepath_creater import join_filepath


def _sanitize_for_json(obj: Any) -> Any:
    """
    Recursively sanitize data structure for JSON serialization.
    Converts NaN, Inf, -Inf to None (null in JSON).

    Args:
        obj: Object to sanitize (dict, list, float, etc.)

    Returns:
        Sanitized object safe for JSON serialization
    """
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    elif isinstance(obj, float):
        # Check for NaN, Infinity, -Infinity
        if math.isnan(obj) or math.isinf(obj):
            return None  # Convert to null in JSON
        return obj
    else:
        return obj


class TimeSeriesChunkHandler:
    """
    Handles reading and writing of timeseries data in chunked format.

    File structure:
        timeseries/
          ├── chunk_0.json      # Records 0-99 (when CHUNK_SIZE=100)
          ├── chunk_1.json      # Records 100-199
          ├── ...
          └── chunk_index_map.json    # Record ID -> Chunk ID mapping

    Configuration:
        CHUNK_SIZE: Number of records per chunk file (default: 100)
                    Can be changed without breaking existing data.
    """

    # Chunk configuration
    # Adjust this value to change how many records are saved per chunk file.
    # Note: Changing this value only affects NEW data; existing data remains readable.
    CHUNK_SIZE = 100

    # File naming constants
    INDEX_MAP_FILENAME = "chunk_index_map.json"
    CHUNK_FILE_PREFIX = "chunk_"

    @classmethod
    def is_chunked_format(cls, dirpath: str) -> bool:
        """
        Check if the directory uses chunked JSON format.

        Args:
            dirpath: Directory path to check

        Returns:
            True if chunked format (chunk_index_map.json exists or chunk files exist)
        """
        # Check for index map file
        index_map_path = join_filepath([dirpath, cls.INDEX_MAP_FILENAME])
        if os.path.exists(index_map_path):
            return True

        # Check for chunk files (auto-recovery case)
        chunk_pattern = join_filepath([dirpath, f"{cls.CHUNK_FILE_PREFIX}*.json"])
        chunk_files = glob(chunk_pattern)
        return len(chunk_files) > 0

    @classmethod
    def save_chunked_data(
        cls,
        dirpath: str,
        record_ids: List[str],
        record_data: List[pd.DataFrame],
    ) -> None:
        """
        Save timeseries data in chunked format.

        Args:
            dirpath: Directory to save chunk files
            record_ids: List of record identifiers (e.g., cell numbers, row indices)
            record_data: List of DataFrames, one per record
        """
        if len(record_ids) != len(record_data):
            raise ValueError("record_ids and record_data must have same length")

        index_map = {}  # Maps record_id to chunk_id
        chunk_data = {}  # Temporary storage: chunk_id -> {record_id -> data}

        for i, (record_id, df) in enumerate(zip(record_ids, record_data)):
            chunk_id = i // cls.CHUNK_SIZE
            index_map[str(record_id)] = chunk_id

            # Add to chunk
            if chunk_id not in chunk_data:
                chunk_data[chunk_id] = {}
            chunk_data[chunk_id][str(record_id)] = df.to_dict(orient="split")

        # Write chunk files
        for chunk_id, records in chunk_data.items():
            chunk_filepath = join_filepath(
                [dirpath, f"{cls.CHUNK_FILE_PREFIX}{chunk_id}.json"]
            )
            # Sanitize data to convert NaN/Inf to null for JSON compliance
            sanitized_records = _sanitize_for_json(records)
            with open(chunk_filepath, "w") as f:
                json.dump(sanitized_records, f, indent=4)

        # Write index mapping file
        index_map_path = join_filepath([dirpath, cls.INDEX_MAP_FILENAME])
        with open(index_map_path, "w") as f:
            json.dump(index_map, f, indent=4)

    @classmethod
    def rebuild_index_map(cls, dirpath: str) -> Dict[str, int]:
        """
        Rebuild index map by scanning all chunk files.

        This is useful for:
        - Recovery when chunk_index_map.json is accidentally deleted
        - Validation/debugging of data integrity
        - Migration from old formats

        Args:
            dirpath: Directory containing chunk files

        Returns:
            Dictionary mapping record_id (str) to chunk_id (int)
        """
        index_map = {}

        # Find all chunk files
        chunk_pattern = join_filepath([dirpath, f"{cls.CHUNK_FILE_PREFIX}*.json"])
        chunk_files = glob(chunk_pattern)

        for chunk_file in chunk_files:
            # Extract chunk_id from filename (e.g., "chunk_0.json" -> 0)
            filename = os.path.basename(chunk_file)
            # Remove file extension (.json)
            filename_without_ext = os.path.splitext(filename)[0]
            # Remove chunk prefix (e.g., "chunk_0" -> "0")
            chunk_id_str = filename_without_ext[len(cls.CHUNK_FILE_PREFIX) :]
            try:
                chunk_id = int(chunk_id_str)
            except ValueError:
                continue  # Skip non-numeric chunk files

            # Load chunk and map all records to this chunk_id
            with open(chunk_file, "r") as f:
                chunk_data = json.load(f)
                for record_id in chunk_data.keys():
                    index_map[record_id] = chunk_id

        return index_map

    @classmethod
    def load_index_map(cls, dirpath: str, auto_rebuild: bool = True) -> Dict[str, int]:
        """
        Load the index mapping file.

        Args:
            dirpath: Directory containing chunk_index_map.json
            auto_rebuild: If True, automatically rebuild index map if missing

        Returns:
            Dictionary mapping record_id (str) to chunk_id (int)

        Raises:
            FileNotFoundError: If index map doesn't exist and auto_rebuild is False
        """
        index_map_path = join_filepath([dirpath, cls.INDEX_MAP_FILENAME])

        # Try to load existing index map
        if os.path.exists(index_map_path):
            with open(index_map_path, "r") as f:
                return json.load(f)

        # Auto-rebuild if enabled
        if auto_rebuild:
            index_map = cls.rebuild_index_map(dirpath)

            # Save the rebuilt index map for future use
            if index_map:
                with open(index_map_path, "w") as f:
                    json.dump(index_map, f, indent=4)

            return index_map

        # Raise error if auto_rebuild is disabled
        raise FileNotFoundError(
            f"Index map file not found: {index_map_path}. "
            f"Set auto_rebuild=True to automatically rebuild from chunk files."
        )

    @classmethod
    def load_chunk_file(cls, dirpath: str, chunk_id: int) -> Dict[str, dict]:
        """
        Load a specific chunk file.

        Args:
            dirpath: Directory containing chunk files
            chunk_id: Chunk ID to load

        Returns:
            Dictionary mapping record_id to record data (in split format)
        """
        chunk_path = join_filepath([dirpath, f"{cls.CHUNK_FILE_PREFIX}{chunk_id}.json"])
        with open(chunk_path, "r") as f:
            return json.load(f)

    @classmethod
    def get_record_data(cls, dirpath: str, record_id: str) -> dict:
        """
        Get data for a specific record from chunked storage.

        Args:
            dirpath: Directory containing chunk files
            record_id: Record identifier to retrieve

        Returns:
            Record data in split format (dict with 'data', 'index', 'columns')

        Raises:
            ValueError: If record_id not found in index map
        """
        index_map = cls.load_index_map(dirpath)
        chunk_id = index_map.get(record_id)

        if chunk_id is None:
            raise ValueError(f"Record ID '{record_id}' not found in index map")

        chunk_data = cls.load_chunk_file(dirpath, chunk_id)
        record_data = chunk_data.get(record_id)

        if record_data is None:
            raise ValueError(f"Record ID '{record_id}' not found in chunk {chunk_id}")

        return record_data

    @classmethod
    def get_all_record_ids(cls, dirpath: str) -> List[str]:
        """
        Get all record IDs from the directory.

        Supports both chunked and legacy formats.

        Args:
            dirpath: Directory to scan

        Returns:
            Sorted list of record IDs (as strings)
        """
        if cls.is_chunked_format(dirpath):
            # Chunked format: read from index map
            index_map = cls.load_index_map(dirpath)
            # Sort numerically if possible, otherwise alphabetically
            return sorted(index_map.keys(), key=lambda x: int(x) if x.isdigit() else x)
        else:
            # Legacy format: scan for individual JSON files
            file_pattern = join_filepath([dirpath, "*.json"])
            filenames = glob(file_pattern)

            # Filter out metadata files
            record_ids = []
            metadata_files = [
                cls.INDEX_MAP_FILENAME,  # chunk_index_map.json
                "plot-meta.json",
            ]
            for filepath in filenames:
                filename = os.path.basename(filepath)
                # Skip metadata files
                if filename in metadata_files:
                    continue
                # Extract record ID from filename
                record_id = os.path.splitext(filename)[0]
                record_ids.append(record_id)

            # Sort numerically if possible
            return sorted(record_ids, key=lambda x: int(x) if x.isdigit() else x)

    @classmethod
    def get_all_chunks(cls, dirpath: str) -> List[int]:
        """
        Get all chunk IDs in the directory.

        Args:
            dirpath: Directory containing chunk files

        Returns:
            Sorted list of chunk IDs
        """
        if not cls.is_chunked_format(dirpath):
            return []

        index_map = cls.load_index_map(dirpath)
        chunk_ids = set(index_map.values())
        return sorted(chunk_ids)

    @classmethod
    def load_all_records(cls, dirpath: str) -> Dict[str, dict]:
        """
        Load all records from chunked storage.

        Args:
            dirpath: Directory containing chunk files

        Returns:
            Dictionary mapping record_id to record data
        """
        all_records = {}

        for chunk_id in cls.get_all_chunks(dirpath):
            chunk_data = cls.load_chunk_file(dirpath, chunk_id)
            all_records.update(chunk_data)

        return all_records

    @classmethod
    def get_chunk_stats(cls, dirpath: str) -> Dict[str, int]:
        """
        Get statistics about chunked data.

        Args:
            dirpath: Directory containing chunk files

        Returns:
            Dictionary with 'total_records', 'num_chunks', 'chunk_size'
        """
        if not cls.is_chunked_format(dirpath):
            # Legacy format
            record_ids = cls.get_all_record_ids(dirpath)
            return {
                "total_records": len(record_ids),
                "num_chunks": len(record_ids),  # Each file is a "chunk"
                "chunk_size": 1,
                "format": "legacy",
            }

        index_map = cls.load_index_map(dirpath)
        chunk_ids = set(index_map.values())

        return {
            "total_records": len(index_map),
            "num_chunks": len(chunk_ids),
            "chunk_size": cls.CHUNK_SIZE,
            "format": "chunked",
        }
