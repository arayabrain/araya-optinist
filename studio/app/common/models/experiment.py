from datetime import datetime
from typing import Dict, Optional

from sqlalchemy import Integer
from sqlalchemy.dialects.mysql import BIGINT
from sqlmodel import JSON, Column, DateTime, Field, ForeignKey, Relationship, String

from studio.app.common.models.base import Base, TimestampMixin
from studio.app.common.schemas.dataview import LocalSyncStatus, PublishStatus


class ExperimentRecord(Base, TimestampMixin, table=True):
    __tablename__ = "experiment_records"

    workspace_id: int = Field(
        sa_column=Column(
            BIGINT(unsigned=True), ForeignKey("workspaces.id"), nullable=False
        ),
    )
    uid: str = Field(sa_column=Column(String(100), nullable=False, index=True))

    name: Optional[str] = Field(sa_column=Column(String(100), nullable=True))

    data_usage: int = Field(
        sa_column=Column(
            BIGINT(unsigned=True), nullable=False, comment="data usage in bytes"
        ),
        default=0,
    )

    thumbnails: Optional[Dict] = Field(default={}, sa_column=Column(JSON))

    success: bool = Field(nullable=False, default=False)

    analyzed_at: Optional[datetime] = Field(sa_column=Column(DateTime(timezone=True)))

    publish_status: int = Field(
        sa_column=Column(
            Integer(),
            nullable=False,
            default=PublishStatus.off.value,
            comment="0: private, 1: public",
        )
    )

    local_sync_status: str = Field(
        sa_column=Column(
            String(20),
            nullable=False,
            default=LocalSyncStatus.synced.value,
            comment="Sync status on local storage: pending, synced, error",
        )
    )

    version: int = Field(
        sa_column=Column(
            Integer(),
            nullable=False,
            default=0,
            comment="Version number for optimistic locking",
        ),
        default=0,
    )

    workspace: Optional["Workspace"] = Relationship(  # noqa: F821
        back_populates="experiments"
    )
