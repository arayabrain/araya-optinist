"""add_display_and_tier_columns_to_subscription_plans

Add display_order, is_featured, and tier columns to subscription_plans table.

Revision ID: g901g9250021
Revises: f801f8250020
Create Date: 2026-01-09 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "g901g9250021"
down_revision = "f801f8250020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add display and tier columns to subscription_plans table.

    Changes:
    - Add display_order column (INTEGER) for UI sorting
    - Add is_featured column (BOOLEAN) to highlight specific plans
    - Add tier column (VARCHAR) for plan tier identification
    - Add index for display_order
    """

    # Add display_order column for UI sorting
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

    # Add is_featured column to highlight specific plans
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

    # Add tier column for plan tier identification
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

    # Create index for display_order
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
