"""add_grace_notification_columns

Add grace-period email notification stamps to subscription_users:
- grace_start_notified_at: when the grace-period start email was sent
- grace_reminder_notified_at: when the grace-period reminder email was sent
- reprocess_notified_at: when the over-quota-again email before a repeat deletion
  pass was sent

Revision ID: q901q1028030
Revises: p901p1020029
Create Date: 2026-10-28 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "q901q1028030"
down_revision = "p901p1020029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_users",
        sa.Column("grace_start_notified_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "subscription_users",
        sa.Column("grace_reminder_notified_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "subscription_users",
        sa.Column("reprocess_notified_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription_users", "reprocess_notified_at")
    op.drop_column("subscription_users", "grace_reminder_notified_at")
    op.drop_column("subscription_users", "grace_start_notified_at")
