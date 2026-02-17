"""add_alert_fix_tables_and_columns

Add tables and columns for alert/edge case fixes from ALERT_FIX_SUMMARY.

Tables created:
- user_deletion_records: Two-phase user deletion tracking (Case 25)
- background_tasks: Persistent background task queue (Case 18)
- storage_operations: Idempotent storage tracking (Cases 16-17)

Columns added:
- experiment_records.deletion_error (Case 14)
- premium_user_assignments.heartbeat_failures (Case 71)

Revision ID: j901j9290024
Revises: i901i9280023
Create Date: 2026-02-06 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "j901j9290024"
down_revision = "i901i9280023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # =========================================================================
    # Case 14: experiment_records.deletion_error
    # =========================================================================
    op.add_column(
        "experiment_records",
        sa.Column(
            "deletion_error",
            sa.String(255),
            nullable=True,
            comment="Error if deletion partially failed (S3 ok, DB not)",
        ),
    )

    # =========================================================================
    # Case 71: premium_user_assignments.heartbeat_failures
    # =========================================================================
    op.add_column(
        "premium_user_assignments",
        sa.Column(
            "heartbeat_failures",
            sa.INTEGER(),
            nullable=False,
            server_default="0",
            comment="Consecutive heartbeat failures, used for grace period",
        ),
    )

    # =========================================================================
    # Cases 16-17: storage_operations table
    # =========================================================================
    op.create_table(
        "storage_operations",
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
            index=True,
        ),
        sa.Column(
            "idempotency_key",
            sa.String(255),
            nullable=False,
            unique=True,
            index=True,
            comment="Unique key to prevent duplicate operations",
        ),
        sa.Column(
            "operation_type",
            sa.Enum("increment", "decrement", name="storage_operation_type_enum"),
            nullable=False,
        ),
        sa.Column(
            "bytes_delta",
            mysql.BIGINT(),
            nullable=False,
            comment="Number of bytes to add or remove",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "completed", "failed", name="storage_operation_status_enum"
            ),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "error_message",
            sa.String(500),
            nullable=True,
            comment="Error message if operation failed",
        ),
        sa.Column(
            "retry_count",
            sa.INTEGER(),
            nullable=False,
            server_default="0",
            comment="Number of retry attempts for failed operations",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # Composite indexes for background job queries
        sa.Index("idx_storage_ops_status_created", "status", "created_at"),
        sa.Index("idx_storage_ops_status_retry", "status", "retry_count"),
    )

    # =========================================================================
    # Case 18: background_tasks table (generic task queue)
    # =========================================================================
    op.create_table(
        "background_tasks",
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
            index=True,
            comment="User who initiated the task",
        ),
        sa.Column(
            "task_type",
            sa.Enum("experiment", "workspace", name="background_task_type_enum"),
            nullable=False,
        ),
        sa.Column(
            "resource_id",
            sa.String(100),
            nullable=False,
            index=True,
            comment="ID of the resource (experiment UID or workspace ID)",
        ),
        sa.Column(
            "workspace_id",
            mysql.BIGINT(unsigned=True),
            nullable=True,
            comment="Workspace ID for experiment tasks",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "in_progress",
                "completed",
                "failed",
                "retrying",
                name="background_task_status_enum",
            ),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column(
            "retry_count",
            sa.INTEGER(),
            nullable=False,
            server_default="0",
            comment="Number of retry attempts",
        ),
        sa.Column(
            "max_retries",
            sa.INTEGER(),
            nullable=False,
            server_default="3",
            comment="Maximum retry attempts before marking as failed",
        ),
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment="Error message if task failed",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(),
            nullable=True,
            comment="When processing started",
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(),
            nullable=True,
            comment="When processing completed",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        # Composite index for background worker queries
        sa.Index("idx_background_tasks_status_created", "status", "created_at"),
    )

    # =========================================================================
    # Case 25: user_deletion_records table
    # =========================================================================
    op.create_table(
        "user_deletion_records",
        sa.Column(
            "id",
            mysql.BIGINT(unsigned=True),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "user_uid",
            sa.String(128),
            nullable=False,
            comment="Firebase UID for recovery checks",
        ),
        sa.Column(
            "step",
            sa.Enum(
                "started",
                "firebase_pending",
                "firebase_deleted",
                "stripe_cancelled",
                "s3_deleted",
                "workspaces_deleted",
                "completed",
                name="deletion_step_enum",
            ),
            nullable=False,
            server_default=sa.text("'started'"),
        ),
        sa.Column(
            "status",
            sa.Enum("in_progress", "completed", "failed", name="deletion_status_enum"),
            nullable=False,
            server_default=sa.text("'in_progress'"),
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment="Error message if deletion failed",
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("idx_user_deletion_records_user_id", "user_id"),
        # Composite index for recovery queries (status + started_at)
        sa.Index("idx_user_deletion_records_status_started", "status", "started_at"),
    )


def downgrade() -> None:
    # Drop tables
    op.drop_table("user_deletion_records")
    op.drop_table("background_tasks")
    op.drop_table("storage_operations")

    # Drop premium_user_assignments column
    op.drop_column("premium_user_assignments", "heartbeat_failures")

    # Drop experiment_records column
    op.drop_column("experiment_records", "deletion_error")
