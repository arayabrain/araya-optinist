"""add_data_existence_flags

Add boolean columns to experiment_records for tracking data existence
per tier (intermediates, outputs, inputs, nwb). Enables the frontend
to show per-tier status and the expiration job to skip already-deleted data.

Revision ID: m901m9320027
Revises: l901l9310026
Create Date: 2026-03-13 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "m901m9320027"
down_revision = "l901l9310026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiment_records",
        sa.Column(
            "has_intermediates",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="Whether intermediate data (function subdirs) exists",
        ),
    )
    op.add_column(
        "experiment_records",
        sa.Column(
            "has_outputs",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="Whether output data (root-level non-YAML) exists",
        ),
    )
    op.add_column(
        "experiment_records",
        sa.Column(
            "has_inputs",
            sa.Boolean(),
            nullable=False,
            server_default="1",
            comment="Whether input data for this experiment's workspace exists",
        ),
    )
    op.add_column(
        "experiment_records",
        sa.Column(
            "has_nwb",
            sa.Boolean(),
            nullable=False,
            server_default="0",
            comment="Whether NWB file exists (DB-authoritative, replaces YAML hasNWB)",
        ),
    )


def downgrade() -> None:
    op.drop_column("experiment_records", "has_nwb")
    op.drop_column("experiment_records", "has_inputs")
    op.drop_column("experiment_records", "has_outputs")
    op.drop_column("experiment_records", "has_intermediates")
