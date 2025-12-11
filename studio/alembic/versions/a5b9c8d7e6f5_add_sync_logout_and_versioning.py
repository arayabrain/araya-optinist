"""add_sync_logout_and_versioning

Adds fields for sync tracking, logout tracking, and optimistic locking:
- local_sync_status to experiment_records for tracking sync state
- logged_out_at to free_user_assignments for logout tracking
- version to experiment_records for optimistic locking

Revision ID: a5b9c8d7e6f5
Revises: f801f8250020
Create Date: 2025-12-10 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a5b9c8d7e6f5"
down_revision = "f801f8250020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add sync, logout, and versioning columns with indexes."""

    # Add local_sync_status to experiment_records
    op.add_column(
        "experiment_records",
        sa.Column(
            "local_sync_status",
            sa.VARCHAR(20),
            nullable=False,
            server_default="synced",
            comment="Sync status on local storage: pending, synced, error",
        ),
    )

    # Add version to experiment_records
    op.add_column(
        "experiment_records",
        sa.Column(
            "version",
            sa.INTEGER,
            nullable=False,
            server_default="0",
            comment="Version number for optimistic locking",
        ),
    )

    # Add logged_out_at to free_user_assignments
    op.add_column(
        "free_user_assignments",
        sa.Column(
            "logged_out_at",
            sa.TIMESTAMP,
            nullable=True,
            comment="Timestamp when user explicitly logged out",
        ),
    )

    # Create indexes
    op.create_index(
        "idx_local_sync_status",
        "experiment_records",
        ["local_sync_status"],
    )

    op.create_index(
        "idx_publish_sync_status",
        "experiment_records",
        ["publish_status", "local_sync_status"],
        unique=False,
    )

    op.create_index(
        "idx_logged_out_at",
        "free_user_assignments",
        ["logged_out_at"],
    )


def downgrade() -> None:
    """Remove all added columns and indexes."""

    op.drop_index("idx_logged_out_at", "free_user_assignments")
    op.drop_index("idx_publish_sync_status", "experiment_records")
    op.drop_index("idx_local_sync_status", "experiment_records")

    op.drop_column("free_user_assignments", "logged_out_at")
    op.drop_column("experiment_records", "version")
    op.drop_column("experiment_records", "local_sync_status")
