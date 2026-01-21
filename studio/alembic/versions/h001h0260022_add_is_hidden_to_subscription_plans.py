"""add_is_hidden_column_to_subscription_plans

Add is_hidden column to subscription_plans table for controlling plan visibility.

Revision ID: h001h0260022
Revises: g901g9250021
Create Date: 2026-01-21 11:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "h001h0260022"
down_revision = "g901g9250021"
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in the table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    """
    Add is_hidden column to subscription_plans table.

    Changes:
    - Add is_hidden column (BOOLEAN) to control plan visibility in UI
    """

    # Add is_hidden column for controlling plan visibility (if not exists)
    if not column_exists("subscription_plans", "is_hidden"):
        op.add_column(
            "subscription_plans",
            sa.Column(
                "is_hidden",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
                comment="Whether this plan should be hidden from the UI",
            ),
        )


def downgrade() -> None:
    """
    Remove is_hidden column from subscription_plans table.
    """

    op.drop_column("subscription_plans", "is_hidden")
