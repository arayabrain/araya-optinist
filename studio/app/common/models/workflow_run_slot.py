from datetime import datetime
from typing import Optional

from sqlalchemy import TIMESTAMP, VARCHAR, Index
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.sql.functions import current_timestamp
from sqlmodel import Column, Field, ForeignKey, SQLModel


class WorkflowRunSlot(SQLModel, table=True):
    """One workflow run counted against its user's concurrent-run limit.

    Written only for a plan with a limit. The run renews heartbeat_at while it
    lives and deletes the row when it ends; a row not renewed within the TTL
    belongs to a run that died and is not counted.
    """

    __tablename__ = "workflow_run_slots"

    __table_args__ = (Index("idx_workflow_run_slots_user_id", "user_id"),)

    id: Optional[int] = Field(
        sa_column=Column(
            BIGINT(unsigned=True),
            primary_key=True,
            nullable=False,
            autoincrement=True,
        ),
        default=None,
    )
    user_id: int = Field(
        sa_column=Column(
            BIGINT(unsigned=True),
            ForeignKey(
                "users.id", name="fk_workflow_run_slot_user", ondelete="CASCADE"
            ),
            nullable=False,
        ),
    )
    workspace_id: str = Field(sa_column=Column(VARCHAR(64), nullable=False))
    unique_id: str = Field(sa_column=Column(VARCHAR(64), nullable=False))
    heartbeat_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP, nullable=False, server_default=current_timestamp())
    )
    created_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP, nullable=False, server_default=current_timestamp())
    )
