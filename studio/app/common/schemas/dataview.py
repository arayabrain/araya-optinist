from datetime import datetime
from enum import Enum
from typing import Generic, List, Optional, TypeVar

from fastapi import Query
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


# TODO: Planned to implement
class DataviewRecordHeader(BaseModel):
    graph_titles: List[str] = []


# TODO: Planned to implement
class ImageInfo(BaseModel):
    urls: List[str]
    thumb_urls: Optional[List[str]]
    params: Optional[dict]

    def __init__(self, urls, params=None, thumb_urls=None):
        if isinstance(urls, str):
            urls = [urls]
        super().__init__(urls=urls, thumb_urls=thumb_urls, params=params)
        if thumb_urls is None:
            self.thumb_urls = [url.replace(".png", ".thumb.png") for url in urls]


class DataviewOwner(BaseModel):
    name: str = None

    class Config:
        orm_mode = True


class DataviewWorkspace(BaseModel):
    id: int
    name: str = None
    user: Optional[DataviewOwner]

    class Config:
        orm_mode = True


class DataviewRecord(BaseModel):
    id: int
    uid: str = None
    owner: Optional[DataviewOwner]
    workspace: Optional[DataviewWorkspace]
    attributes: Optional[dict] = {}
    publish_status: int = 0
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        orm_mode = True


class DataviewRecordSearchOptions(BaseModel):
    uid: Optional[str] = Field(
        default="", description="partial match (experiment_records.uid)"
    )
    user_name: Optional[str] = Field(
        default="", description="partial match (user.name)"
    )
    workspace_id: Optional[str] = Field(default="", description="workspace.id")
    workspace_name: Optional[str] = Field(
        default="", description="partial match (workspace.name)"
    )
