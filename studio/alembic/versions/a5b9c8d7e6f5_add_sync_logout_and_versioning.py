"""add_sync_and_versioning

Adds fields for sync tracking and optimistic locking:
- local_sync_status to experiment_records for tracking sync state
- version to experiment_records for optimistic locking

Revision ID: a5b9c8d7e6f5
Revises: f801f8250020
Create Date: 2025-12-10 10:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision = "a5b9c8d7e6f5"
down_revision = "f801f8250020"
branch_labels = None
depends_on = None


def column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def index_exists(table_name: str, index_name: str) -> bool:
    """Check if an index exists on a table."""
    bind = op.get_bind()
    inspector = inspect(bind)
    indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    """Add sync, logout, and versioning columns with indexes."""

    # Add local_sync_status to experiment_records if it doesn't exist
    if not column_exists("experiment_records", "local_sync_status"):
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

    # Add version to experiment_records if it doesn't exist
    if not column_exists("experiment_records", "version"):
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

    # Note: logged_out_at is now created directly in f801f8250020
    # (merged from this migration for cleaner schema)

    # Create indexes if they don't exist
    if not index_exists("experiment_records", "idx_local_sync_status"):
        op.create_index(
            "idx_local_sync_status",
            "experiment_records",
            ["local_sync_status"],
        )

    if not index_exists("experiment_records", "idx_publish_sync_status"):
        op.create_index(
            "idx_publish_sync_status",
            "experiment_records",
            ["publish_status", "local_sync_status"],
            unique=False,
        )


def downgrade() -> None:
    """Remove all added columns and indexes."""

    # Note: idx_logged_out_at and logged_out_at column are managed in f801f8250020

    # Drop indexes if they exist
    if index_exists("experiment_records", "idx_publish_sync_status"):
        op.drop_index("idx_publish_sync_status", "experiment_records")

    if index_exists("experiment_records", "idx_local_sync_status"):
        op.drop_index("idx_local_sync_status", "experiment_records")

    # Drop columns if they exist
    if column_exists("experiment_records", "version"):
        op.drop_column("experiment_records", "version")

    if column_exists("experiment_records", "local_sync_status"):
        op.drop_column("experiment_records", "local_sync_status")
