"""add_display_and_tier_columns_to_subscription_plans

Add display_order, is_featured, and tier columns to subscription_plans table.

Revision ID: g901g9250021
Revises: a5b9c8d7e6f5
Create Date: 2026-01-09 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "g901g9250021"
down_revision = "a5b9c8d7e6f5"
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in the table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(table_name: str, index_name: str) -> bool:
    """Check if an index exists on the table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    """
    Add display and tier columns to subscription_plans table.

    Changes:
    - Add display_order column (INTEGER) for UI sorting
    - Add is_featured column (BOOLEAN) to highlight specific plans
    - Add tier column (VARCHAR) for plan tier identification
    - Add index for display_order
    """

    # Add display_order column for UI sorting (if not exists)
    if not column_exists("subscription_plans", "display_order"):
        op.add_column(
            "subscription_plans",
            sa.Column(
                "display_order",
                sa.Integer(),
                nullable=False,
                server_default="0",
                comment="Display order for plan selection UI (lower = shown first)",
            ),
        )

    # Add is_featured column to highlight specific plans (if not exists)
    if not column_exists("subscription_plans", "is_featured"):
        op.add_column(
            "subscription_plans",
            sa.Column(
                "is_featured",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
                comment="Whether this plan should be highlighted in UI",
            ),
        )

    # Add tier column for plan tier identification (if not exists)
    if not column_exists("subscription_plans", "tier"):
        op.add_column(
            "subscription_plans",
            sa.Column(
                "tier",
                sa.String(50),
                nullable=False,
                server_default="free",
                comment="Plan tier identifier (e.g., 'free', 'premium', 'enterprise')",
            ),
        )

    # Update existing plans with appropriate tier values based on price
    op.execute(
        """
        UPDATE subscription_plans
        SET tier = CASE
            WHEN price = 0 THEN 'free'
            WHEN price > 0 THEN 'premium'
        END
        WHERE tier = 'free'
        """
    )

    # Create index for display_order (if not exists)
    if not index_exists("subscription_plans", "idx_subscription_plans_display_order"):
        op.create_index(
            "idx_subscription_plans_display_order",
            "subscription_plans",
            ["display_order"],
        )


def downgrade() -> None:
    """
    Remove display and tier columns from subscription_plans table.
    """

    # Drop index
    op.drop_index(
        "idx_subscription_plans_display_order", table_name="subscription_plans"
    )

    # Drop columns in reverse order
    op.drop_column("subscription_plans", "tier")
    op.drop_column("subscription_plans", "is_featured")
    op.drop_column("subscription_plans", "display_order")
