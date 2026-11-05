"""add_experiment_hidden_outputs

Add hidden_outputs to experiment_records: a JSON list of normalized output
paths the owner hides from public Dataview viewers.

Revision ID: r901r1104031
Revises: q901q1028030
Create Date: 2026-11-04 12:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "r901r1104031"
down_revision = "q901q1028030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiment_records",
        sa.Column("hidden_outputs", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("experiment_records", "hidden_outputs")
