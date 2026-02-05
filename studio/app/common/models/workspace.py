from datetime import datetime
from enum import Enum
from typing import List, Optional

from sqlalchemy import DateTime
from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Text
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.sql.functions import current_timestamp
from sqlmodel import Column, Field, ForeignKey, Relationship, String, UniqueConstraint

from studio.app.common.models.base import Base, TimestampMixin


class WorkspaceStatus(str, Enum):
    """Status enum for workspace lifecycle management."""

    ACTIVE = "active"
    DELETING = "deleting"
    PARTIAL_DELETE = "partial_delete"
    DELETED = "deleted"


class WorkspacesShareUser(Base, table=True):
    __tablename__ = "workspaces_share_users"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="idx_workspace_id_user_id"),
    )

    workspace_id: int = Field(
        sa_column=Column(
            BIGINT(unsigned=True), ForeignKey("workspaces.id"), nullable=False
        ),
    )
    user_id: int = Field(
        sa_column=Column(BIGINT(unsigned=True), ForeignKey("users.id"), nullable=False),
    )
    created_at: Optional[datetime] = Field(
        sa_column_kwargs={"server_default": current_timestamp()},
    )


class Workspace(Base, TimestampMixin, table=True):
    __tablename__ = "workspaces"

    name: str = Field(sa_column=Column(String(100), nullable=False))
    user_id: int = Field(
        sa_column=Column(
            BIGINT(unsigned=True), ForeignKey("users.id", name="user"), nullable=False
        ),
    )
    deleted: bool = Field(nullable=False)
    status: WorkspaceStatus = Field(
        sa_column=Column(
            SQLEnum(WorkspaceStatus),
            nullable=False,
            default=WorkspaceStatus.ACTIVE,
            server_default=WorkspaceStatus.ACTIVE.value,
        ),
        default=WorkspaceStatus.ACTIVE,
    )
    deleted_at: Optional[datetime] = Field(
        sa_column=Column(DateTime, nullable=True),
        default=None,
    )
    input_data_usage: int = Field(
        sa_column=Column(
            BIGINT(unsigned=True), nullable=False, comment="data usage in bytes"
        ),
        default=0,
    )
    deletion_error: Optional[str] = Field(
        sa_column=Column(
            Text,
            nullable=True,
            default=None,
            comment="Error details if deletion partially failed",
        ),
        default=None,
    )
    failed_experiment_uids: Optional[str] = Field(
        sa_column=Column(
            Text,
            nullable=True,
            default=None,
            comment="Comma-separated UIDs of experiments that failed to delete",
        ),
        default=None,
    )

    user: Optional["User"] = Relationship(back_populates="workspace")  # noqa: F821
    user_share: List["User"] = Relationship(  # noqa: F821
        back_populates="workspace_share", link_model=WorkspacesShareUser
    )
    experiments: List["ExperimentRecord"] = Relationship(  # noqa: F821
        back_populates="workspace"
    )
