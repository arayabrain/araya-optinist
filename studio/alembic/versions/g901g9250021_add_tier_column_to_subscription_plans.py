"""add_tier_column_to_subscription_plans

Add tier and related columns to subscription_plans table for flexible plan management.
This enables data-driven plan configuration without hardcoded plan ID checks.

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
    Add tier column and related fields to subscription_plans table.

    Changes:
    - Add tier column (VARCHAR(50)) to identify plan level
      (free, premium, enterprise, etc.)
    - Add display_order column (INTEGER) for UI sorting
    - Add is_featured column (BOOLEAN) to highlight specific plans
    - Add max_storage_gb column (INTEGER) for storage quota configuration
    - Add description column (TEXT) for plan description
    - Add metadata column (JSON) for extensible plan attributes
    - Add indexes for performance
    - Update existing data with tier values
    """

    # Add tier column with default value 'free'
    op.add_column(
        "subscription_plans",
        sa.Column(
            "tier",
            sa.String(length=50),
            nullable=False,
            server_default="free",
            comment="Plan tier identifier (free, premium, enterprise, etc.)",
        ),
    )

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

    # Add max_storage_gb column for storage quota
    op.add_column(
        "subscription_plans",
        sa.Column(
            "max_storage_gb",
            sa.Integer(),
            nullable=True,
            comment="Maximum storage quota in GB for this plan",
        ),
    )

    # Add description column for plan details
    op.add_column(
        "subscription_plans",
        sa.Column(
            "description",
            sa.Text(),
            nullable=True,
            comment="Detailed description of the plan",
        ),
    )

    # Add metadata column for extensible attributes
    op.add_column(
        "subscription_plans",
        sa.Column(
            "metadata",
            sa.JSON(),
            nullable=True,
            comment="Extensible metadata field for future plan attributes",
        ),
    )

    # Create indexes for performance
    op.create_index(
        "idx_subscription_plans_tier",
        "subscription_plans",
        ["tier"],
    )

    op.create_index(
        "idx_subscription_plans_display_order",
        "subscription_plans",
        ["display_order"],
    )

    # Update existing plans based on their characteristics (adaptable approach)
    # This works for any number of existing plans, not just 2

    # Set tier for free plans (price = 0 or name contains 'free')
    op.execute(
        """
        UPDATE subscription_plans
        SET tier = 'free',
            max_storage_gb = COALESCE(max_storage_gb, 5),
            display_order = COALESCE(display_order, 1),
            description = COALESCE(
                description,
                'Free plan with basic features and 5GB storage'
            )
        WHERE (price = 0 OR LOWER(name) LIKE '%free%')
          AND tier = 'free'
        """
    )

    # Set tier for premium plans (price > 0 and name contains 'premium')
    op.execute(
        """
        UPDATE subscription_plans
        SET tier = 'premium',
            max_storage_gb = COALESCE(max_storage_gb, 200),
            display_order = COALESCE(display_order, 2),
            is_featured = COALESCE(is_featured, TRUE),
            description = COALESCE(
                description,
                'Premium plan with advanced features and 200GB storage'
            )
        WHERE (price > 0 AND LOWER(name) LIKE '%premium%')
          AND tier = 'free'
        """
    )

    # Set tier for enterprise plans (if any exist)
    op.execute(
        """
        UPDATE subscription_plans
        SET tier = 'enterprise',
            max_storage_gb = NULL,
            display_order = COALESCE(display_order, 3),
            description = COALESCE(
                description,
                'Enterprise plan with unlimited features'
            )
        WHERE LOWER(name) LIKE '%enterprise%'
          AND tier = 'free'
        """
    )


def downgrade() -> None:
    """
    Remove tier column and related fields from subscription_plans table.
    """

    # Drop indexes
    op.drop_index(
        "idx_subscription_plans_display_order", table_name="subscription_plans"
    )
    op.drop_index("idx_subscription_plans_tier", table_name="subscription_plans")

    # Drop columns in reverse order
    op.drop_column("subscription_plans", "metadata")
    op.drop_column("subscription_plans", "description")
    op.drop_column("subscription_plans", "max_storage_gb")
    op.drop_column("subscription_plans", "is_featured")
    op.drop_column("subscription_plans", "display_order")
    op.drop_column("subscription_plans", "tier")
