from datetime import datetime
from typing import Optional

from sqlalchemy import INTEGER, TIMESTAMP, VARCHAR
from sqlalchemy.sql.functions import current_timestamp
from sqlmodel import Column, Field, SQLModel


class FreeUserAssignment(SQLModel, table=True):
    __tablename__ = "free_user_assignments"

    user_id: str = Field(
        sa_column=Column(VARCHAR(255), primary_key=True, nullable=False)
    )
    instance_id: str = Field(sa_column=Column(VARCHAR(20), nullable=False))
    assigned_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP, nullable=False, server_default=current_timestamp())
    )
    last_activity: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP,
            nullable=False,
            server_default=current_timestamp(),
        )
    )
    active_workflow_count: int = Field(
        sa_column=Column(
            INTEGER,
            nullable=False,
            server_default="0",
            comment="Number of active workflows running for this user",
        ),
        default=0,
    )
    last_workflow_start: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP, nullable=True, comment="Timestamp of last workflow start"
        ),
        default=None,
    )
    last_workflow_end: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP, nullable=True, comment="Timestamp of last workflow completion"
        ),
        default=None,
    )
    migration_count: int = Field(
        sa_column=Column(
            INTEGER,
            nullable=False,
            server_default="0",
            comment="Number of times user has been migrated between instances",
        ),
        default=0,
    )
    last_migration: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP, nullable=True, comment="Timestamp of last migration event"
        ),
        default=None,
    )
    logged_out_at: Optional[datetime] = Field(
        sa_column=Column(
            TIMESTAMP,
            nullable=True,
            comment="Timestamp when user explicitly logged out",
        ),
        default=None,
    )
