"""create_premium_management_system

Comprehensive migration for Premium Management System.
Combines: b301b4120016, c501c5230017, d601d6240018

Revision ID: e701e7250019
Revises: 61f6f5b6d03f
Create Date: 2025-09-18 16:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "e701e7250019"
down_revision = "61f6f5b6d03f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create complete premium_user_assignments table with all
    features for premium management system."""

    # Create premium_user_assignments table with all columns
    op.create_table(
        "premium_user_assignments",
        # Primary key
        sa.Column(
            "id", mysql.BIGINT(unsigned=True), nullable=False, autoincrement=True
        ),
        # Original table columns (b301b4120016)
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("instance_id", sa.VARCHAR(20), nullable=False),
        sa.Column("target_group_arn", sa.VARCHAR(512), nullable=False),
        sa.Column("alb_rule_arn", sa.VARCHAR(512), nullable=False),
        sa.Column(
            "assigned_at",
            sa.TIMESTAMP,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "status",
            sa.Enum("active", "migrating", "terminating", name="assignment_status"),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "last_activity",
            sa.TIMESTAMP,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        # Instance state tracking columns (c501c5230017)
        sa.Column(
            "instance_state",
            sa.Enum(
                "launching",
                "running",
                "stopping",
                "stopped",
                "terminating",
                name="instance_state",
            ),
            nullable=False,
            server_default="launching",
        ),
        sa.Column(
            "is_shared",
            sa.Boolean,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "assignment_attempts",
            sa.INTEGER,
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "last_state_check",
            sa.TIMESTAMP,
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "is_standby",
            sa.Boolean,
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "standby_created_at",
            sa.TIMESTAMP,
            nullable=True,
        ),
        # Workflow tracking columns (added 2025-12-22)
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
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_premium_user"),
        sa.UniqueConstraint("user_id", name="uq_premium_user_id"),
    )

    # Create all indexes
    # Original indexes (b301b4120016)
    op.create_index("idx_instance_id", "premium_user_assignments", ["instance_id"])
    op.create_index("idx_last_activity", "premium_user_assignments", ["last_activity"])
    op.create_index("idx_status", "premium_user_assignments", ["status"])

    # Instance state tracking indexes (c501c5230017)
    op.create_index(
        "idx_instance_state", "premium_user_assignments", ["instance_state"]
    )
    op.create_index("idx_is_shared", "premium_user_assignments", ["is_shared"])
    op.create_index(
        "idx_last_state_check", "premium_user_assignments", ["last_state_check"]
    )
    op.create_index("idx_is_standby", "premium_user_assignments", ["is_standby"])
    op.create_index(
        "idx_standby_created_at", "premium_user_assignments", ["standby_created_at"]
    )

    # Workflow tracking index (added 2025-12-22)
    op.create_index(
        "idx_workflow_recovery",
        "premium_user_assignments",
        ["active_workflow_count", "last_workflow_start"],
    )


def downgrade() -> None:
    """Drop the entire premium_user_assignments table and all related objects."""

    # Drop all indexes first

    # Workflow tracking index (added 2025-12-22)
    op.drop_index("idx_workflow_recovery", "premium_user_assignments")

    # Instance state tracking indexes (c501c5230017)
    op.drop_index("idx_standby_created_at", "premium_user_assignments")
    op.drop_index("idx_is_standby", "premium_user_assignments")
    op.drop_index("idx_last_state_check", "premium_user_assignments")
    op.drop_index("idx_is_shared", "premium_user_assignments")
    op.drop_index("idx_instance_state", "premium_user_assignments")

    # Original indexes (b301b4120016)
    op.drop_index("idx_status", "premium_user_assignments")
    op.drop_index("idx_last_activity", "premium_user_assignments")
    op.drop_index("idx_instance_id", "premium_user_assignments")

    # Drop table (this also drops all columns automatically)
    op.drop_table("premium_user_assignments")

    # Drop enum types (MySQL handles this automatically,
    # but PostgreSQL might need explicit drops)
    # op.execute("DROP TYPE IF EXISTS assignment_status")
    # op.execute("DROP TYPE IF EXISTS instance_state")
