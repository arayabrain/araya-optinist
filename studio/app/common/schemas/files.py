from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from pydantic.dataclasses import dataclass as pydantic_dataclass
from pydantic.networks import AnyHttpUrl


class SyncStatus(str, Enum):
    LOCAL = "local"  # Only on local disk (not uploaded)
    SYNCED = "synced"  # Exists both locally and in S3
    REMOTE = "remote"  # Only in S3 (needs download before run)


@pydantic_dataclass
class TreeNode:
    path: str
    name: str
    isdir: bool
    nodes: List["TreeNode"]
    shape: Optional[List] = None


@pydantic_dataclass
class TreeNodeWithSync:
    path: str
    name: str
    isdir: bool
    nodes: List["TreeNodeWithSync"]
    shape: Optional[List] = None
    sync_status: SyncStatus = SyncStatus.SYNCED
    size: Optional[int] = None


@dataclass
class FilePath:
    file_path: str


@dataclass
class DownloadFileRequest:
    url: AnyHttpUrl


@dataclass
class DownloadStatus:
    total: int = 0
    current: int = 0
    error: Optional[str] = None
