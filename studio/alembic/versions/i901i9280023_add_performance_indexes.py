"""add_performance_indexes

Add database indexes for frequently queried columns to improve query performance.

These indexes address:
- Subscription lookups by user_id and expiration (used on every authenticated request)
- Subscription purchase lookups for cancellation checks
- Subscription cancellation lookups by purchase_id
- Workspace queries filtering by user_id and deleted status
- User queries filtering by organization_id

Expected impact:
- 10-100x faster lookups on indexed columns
- Significant reduction in full table scans
- Improved response times for subscription checks and user context loading

Revision ID: i901i9280023
Revises: h901h9270022
Create Date: 2025-01-19 10:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "i901i9280023"
down_revision = "h901h9270022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add performance indexes for frequently queried columns."""

    # =========================================================================
    # Subscription Users Table Indexes
    # =========================================================================
    # These are critical for subscription checks that happen on every request

    # Composite index for subscription lookups (user_id + expiration)
    # Used by: get_user_subscription(), is_subscription_cancelled()
    # Query pattern: WHERE user_id = ? AND expiration > NOW()
    op.create_index(
        "idx_subscription_users_user_expiration",
        "subscription_users",
        ["user_id", "expiration"],
        unique=False,
    )

    # =========================================================================
    # Subscription User Purchases Table Indexes
    # =========================================================================
    # Used for cancellation status checks

    # Index for purchase lookups by user_id
    # Used by: is_subscription_cancelled() to find purchase records
    # Query pattern: WHERE user_id = ? AND plan_id = ? ORDER BY created_at DESC
    op.create_index(
        "idx_subscription_user_purchases_user_id",
        "subscription_user_purchases",
        ["user_id"],
        unique=False,
    )

    # Composite index for more specific purchase lookups
    # Query pattern: WHERE user_id = ? AND plan_id = ? AND created_at <= ?
    op.create_index(
        "idx_subscription_user_purchases_user_plan_created",
        "subscription_user_purchases",
        ["user_id", "plan_id", "created_at"],
        unique=False,
    )

    # =========================================================================
    # Subscription Cancellations Table Indexes
    # =========================================================================
    # Used to check if a subscription purchase has been cancelled

    # Index for cancellation lookups by purchase_id
    # Used by: is_subscription_cancelled() final check
    # Query pattern: WHERE purchases_id = ?
    op.create_index(
        "idx_subscription_cancellations_purchases_id",
        "subscription_cancellations",
        ["purchases_id"],
        unique=False,
    )

    # =========================================================================
    # Workspaces Table Indexes
    # =========================================================================
    # Used for capacity calculations in user context loading

    # Composite index for workspace queries by user and deleted status
    # Used by: capacity subqueries in __get_current_user_record(), list_user()
    # Query pattern: WHERE user_id = ? AND deleted = FALSE
    op.create_index(
        "idx_workspaces_user_deleted",
        "workspaces",
        ["user_id", "deleted"],
        unique=False,
    )

    # =========================================================================
    # Users Table Indexes
    # =========================================================================
    # Used for user listing and filtering

    # Index for user queries by organization_id
    # Used by: list_user() admin panel
    # Query pattern: WHERE organization_id = ? AND active = TRUE
    op.create_index(
        "idx_users_organization_id",
        "users",
        ["organization_id"],
        unique=False,
    )

    # Composite index for active users in organization
    # Query pattern: WHERE organization_id = ? AND active = TRUE
    op.create_index(
        "idx_users_organization_active",
        "users",
        ["organization_id", "active"],
        unique=False,
    )

    # =========================================================================
    # User Storage Usage Table Indexes
    # =========================================================================
    # Note: user_id likely already has unique constraint, but adding explicit
    # index if not present for clarity

    # Index for storage usage lookups by user_id
    # Used by: __get_current_user_record() to get storage stats
    op.create_index(
        "idx_user_storage_usage_user_id",
        "user_storage_usage",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove performance indexes."""

    # User Storage Usage indexes
    op.drop_index("idx_user_storage_usage_user_id", "user_storage_usage")

    # Users indexes
    op.drop_index("idx_users_organization_active", "users")
    op.drop_index("idx_users_organization_id", "users")

    # Workspaces indexes
    op.drop_index("idx_workspaces_user_deleted", "workspaces")

    # Subscription Cancellations indexes
    op.drop_index(
        "idx_subscription_cancellations_purchases_id", "subscription_cancellations"
    )

    # Subscription User Purchases indexes
    op.drop_index(
        "idx_subscription_user_purchases_user_plan_created",
        "subscription_user_purchases",
    )
    op.drop_index(
        "idx_subscription_user_purchases_user_id", "subscription_user_purchases"
    )

    # Subscription Users indexes
    op.drop_index("idx_subscription_users_user_expiration", "subscription_users")
