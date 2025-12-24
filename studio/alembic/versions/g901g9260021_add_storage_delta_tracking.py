"""add_storage_delta_tracking

Revision ID: g901g9260021
Revises: f801f8250020
Create Date: 2025-12-23 17:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "g901g9260021"
down_revision = "f801f8250020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add delta_since_last_scan column to track cumulative changes
    op.add_column(
        "user_storage_usage",
        sa.Column(
            "delta_since_last_scan",
            sa.BIGINT,
            nullable=False,
            server_default="0",
            comment="Cumulative bytes changed since last full S3 scan",
        ),
    )

    # Add last_full_scan column to track when last S3 reconciliation occurred
    op.add_column(
        "user_storage_usage",
        sa.Column(
            "last_full_scan",
            sa.DateTime,
            nullable=True,  # NULL means never scanned
            comment="Timestamp of last full S3 storage scan",
        ),
    )

    # Initialize last_full_scan to NULL for all existing records
    # (will be set on first reconciliation)


def downgrade() -> None:
    op.drop_column("user_storage_usage", "last_full_scan")
    op.drop_column("user_storage_usage", "delta_since_last_scan")
