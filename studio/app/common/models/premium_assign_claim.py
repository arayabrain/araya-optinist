from datetime import datetime
from typing import Optional

from sqlalchemy import TIMESTAMP, VARCHAR
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.sql.functions import current_timestamp
from sqlmodel import Column, Field, ForeignKey, SQLModel


class PremiumAssignClaim(SQLModel, table=True):
    """Per-user claim serializing concurrent premium assigns.

    Written only by the premium manager Lambda; a row whose expires_at has
    passed belongs to a finished or crashed assign and is reclaimable.
    """

    __tablename__ = "premium_assign_claims"

    user_id: int = Field(
        sa_column=Column(
            BIGINT(unsigned=True),
            ForeignKey(
                "users.id", name="fk_premium_assign_claim_user", ondelete="CASCADE"
            ),
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
    )
    claim_token: str = Field(sa_column=Column(VARCHAR(32), nullable=False))
    claimed_at: Optional[datetime] = Field(
        sa_column=Column(TIMESTAMP, nullable=False, server_default=current_timestamp())
    )
    expires_at: datetime = Field(sa_column=Column(TIMESTAMP, nullable=False))
