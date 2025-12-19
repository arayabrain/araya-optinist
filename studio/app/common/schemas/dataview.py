from datetime import datetime
from enum import Enum
from typing import Generic, Optional, TypeVar

from fastapi_pagination import LimitOffsetPage
from pydantic import BaseModel, Field

T = TypeVar("T")


class PageWithHeader(LimitOffsetPage[T], Generic[T]):
    header: Optional["DataviewRecordHeader"] = {}


class PublishFlags(str, Enum):
    on = "on"
    off = "off"


class PublishStatus(int, Enum):
    on = 1
    off = 0


class LocalSyncStatus(str, Enum):
    pending = "pending"  # Experiment published but not yet synced to all instances
    synced = "synced"  # Experiment available on local storage
    error = "error"  # Sync failed, needs retry


class DataviewRecordHeader(BaseModel):
    workspace_id: Optional[int]
    workspace_name: Optional[str]


class DataviewOwner(BaseModel):
    name: Optional[str]

    class Config:
        orm_mode = True


class DataviewWorkspace(BaseModel):
    id: int
    name: Optional[str]
    user: Optional[DataviewOwner]

    class Config:
        orm_mode = True


class DataviewThumbnails(BaseModel):
    image_url: Optional[str]
    roi_url: Optional[str]


class DataviewRecord(BaseModel):
    id: int
    uid: Optional[str]
    name: Optional[str]
    owner: Optional[DataviewOwner]
    workspace: Optional[DataviewWorkspace]
    attributes: Optional[dict] = {}
    thumbnails: Optional[DataviewThumbnails]
    analyzed_at: Optional[datetime]
    publish_status: int = 0
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        orm_mode = True


class DataviewRecordSearchOptions(BaseModel):
    uid: Optional[str] = Field(
        default="", description="partial match (experiment_records.uid)"
    )
    name: Optional[str] = Field(
        default="", description="partial match (experiment_records.name)"
    )
    user_name: Optional[str] = Field(
        default="", description="partial match (user.name)"
    )
    workspace_id: Optional[int] = Field(default=None, description="workspace.id")
    workspace_name: Optional[str] = Field(
        default="", description="partial match (workspace.name)"
    )
    publish_status: Optional[int] = Field(default=None, description="")
