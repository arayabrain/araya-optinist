"""add_subscription_users_trial_end

Add trial_end to subscription_users: the Stripe subscription's trial end, copied
by the webhooks so the app can tell a running free trial from a paid period.

Revision ID: s901s1117032
Revises: r901r1104031
Create Date: 2026-11-17 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "s901s1117032"
down_revision = "r901r1104031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_users",
        sa.Column("trial_end", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscription_users", "trial_end")
