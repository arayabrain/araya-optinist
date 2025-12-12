"""create_free_user_tracking_system

Comprehensive migration for Free User Tracking System.
Enables activity tracking, load balancing, and autoscaling for free tier users.

Revision ID: f801f8250020
Revises: e701e7250019
Create Date: 2025-11-14 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f801f8250020"
down_revision = "e701e7250019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create complete free_user_assignments table with all
    features for free user tracking and load balancing system."""

    # Create free_user_assignments table with all columns
    op.create_table(
        "free_user_assignments",
        # Core assignment columns
        sa.Column("user_id", sa.VARCHAR(255), primary_key=True, nullable=False),
        sa.Column("instance_id", sa.VARCHAR(20), nullable=False),
        sa.Column(
            "assigned_at",
            sa.TIMESTAMP,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        # Activity tracking
        sa.Column(
            "last_activity",
            sa.TIMESTAMP,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        # Workflow tracking for safe migration
        sa.Column(
            "active_workflow_count",
            sa.INTEGER,
            nullable=False,
            server_default="0",
            comment="Number of active workflows running for this user",
        ),
        sa.Column(
            "last_workflow_start",
            sa.TIMESTAMP,
            nullable=True,
            comment="Timestamp of last workflow start",
        ),
        sa.Column(
            "last_workflow_end",
            sa.TIMESTAMP,
            nullable=True,
            comment="Timestamp of last workflow completion",
        ),
        # Migration tracking
        sa.Column(
            "migration_count",
            sa.INTEGER,
            nullable=False,
            server_default="0",
            comment="Number of times user has been migrated between instances",
        ),
        sa.Column(
            "last_migration",
            sa.TIMESTAMP,
            nullable=True,
            comment="Timestamp of last migration event",
        ),
    )

    # Create indexes for efficient queries
    op.create_index("idx_instance_id", "free_user_assignments", ["instance_id"])
    op.create_index("idx_last_activity", "free_user_assignments", ["last_activity"])
    op.create_index(
        "idx_active_workflow_count", "free_user_assignments", ["active_workflow_count"]
    )
    op.create_index(
        "idx_last_workflow_start", "free_user_assignments", ["last_workflow_start"]
    )

    # Composite index for finding idle users (used by Free Manager Lambda)
    op.create_index(
        "idx_idle_users",
        "free_user_assignments",
        ["active_workflow_count", "last_activity"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the entire free_user_assignments table and all related objects."""

    # Drop all indexes first
    op.drop_index("idx_idle_users", "free_user_assignments")
    op.drop_index("idx_last_workflow_start", "free_user_assignments")
    op.drop_index("idx_active_workflow_count", "free_user_assignments")
    op.drop_index("idx_last_activity", "free_user_assignments")
    op.drop_index("idx_instance_id", "free_user_assignments")

    # Drop table
    op.drop_table("free_user_assignments")
