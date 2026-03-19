"""add_instance_usage_log

Track per-user session hours for usage-based cost reporting.

Table created:
- instance_usage_log: Records user sessions with start/end timestamps

Revision ID: k901k9300025
Revises: j901j9290024
Create Date: 2026-03-06 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "k901k9300025"
down_revision = "j901j9290024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "instance_usage_log",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            mysql.BIGINT(unsigned=True),
            nullable=False,
        ),
        sa.Column(
            "instance_id",
            sa.VARCHAR(20),
            nullable=False,
        ),
        sa.Column(
            "tier",
            sa.Enum("free", "premium", name="usage_tier_enum"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "ended_at",
            sa.TIMESTAMP(),
            nullable=True,
            comment="NULL means session is still active",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("idx_usage_log_user_tier", "user_id", "tier"),
        sa.Index("idx_usage_log_active", "ended_at"),
        sa.Index("idx_usage_log_tier_started", "tier", "started_at"),
    )


def downgrade() -> None:
    op.drop_table("instance_usage_log")
