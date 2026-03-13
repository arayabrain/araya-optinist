"""add_expiration_deletion

Add infrastructure for expiration lifecycle deletion:
- user_preferences table for deletion priority preference
- deletion_processed_at column on subscription_users for lifecycle tracking
- Data existence flag columns on experiment_records for per-tier status

Revision ID: l901l9310026
Revises: 33e781982125
Create Date: 2026-03-11 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "l901l9310026"
down_revision = "33e781982125"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- user_preferences table ---
    op.create_table(
        "user_preferences",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "deletion_priority",
            sa.Enum(
                "preserve_outputs",
                "preserve_inputs",
                name="deletion_priority_enum",
            ),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.func.current_timestamp(),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_user_preferences_user"
        ),
    )
    op.create_index("ix_user_preferences_user_id", "user_preferences", ["user_id"])

    # --- subscription_users: deletion tracking ---
    op.add_column(
        "subscription_users",
        sa.Column(
            "deletion_processed_at",
            sa.DateTime(),
            nullable=True,
        ),
    )

    # --- experiment_records: data existence flags ---
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
    op.drop_column("subscription_users", "deletion_processed_at")
    op.drop_index("ix_user_preferences_user_id", table_name="user_preferences")
    op.drop_table("user_preferences")
    sa.Enum(name="deletion_priority_enum").drop(op.get_bind(), checkfirst=True)
