"""add per-plan workflow concurrency limit and workflow run slots

- subscription_plans.max_concurrent_workflows: NULL = no limit, so existing
  plans keep today's behaviour
- workflow_run_slots: one row per run counted against its user's limit; a row
  not renewed within the TTL belongs to a run that died

Revision ID: q901q1027030
Revises: p901p1020029
Create Date: 2026-10-27 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "q901q1027030"
down_revision = "p901p1020029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column(
            "max_concurrent_workflows",
            sa.INTEGER(),
            nullable=True,
            comment="Concurrent workflow runs per user of this plan; NULL = no limit",
        ),
    )
    op.create_table(
        "workflow_run_slots",
        sa.Column(
            "id", mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False
        ),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("workspace_id", sa.VARCHAR(64), nullable=False),
        sa.Column("unique_id", sa.VARCHAR(64), nullable=False),
        sa.Column(
            "heartbeat_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_workflow_run_slot_user",
            ondelete="CASCADE",
        ),
    )
    op.create_index("idx_workflow_run_slots_user_id", "workflow_run_slots", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_workflow_run_slots_user_id", table_name="workflow_run_slots")
    op.drop_table("workflow_run_slots")
    op.drop_column("subscription_plans", "max_concurrent_workflows")
