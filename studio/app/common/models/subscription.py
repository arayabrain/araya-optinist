from datetime import datetime
from typing import Optional

from sqlalchemy import BIGINT, String, UniqueConstraint
from sqlalchemy.sql import func
from sqlmodel import Column, Field, SQLModel


class SubscriptionPlans(SQLModel, table=True):
    __tablename__ = "subscription_plans"
    __table_args__ = (UniqueConstraint("id", name="idx_id"),)

    id: Optional[int] = Field(
        sa_column=Column(BIGINT, primary_key=True, nullable=False), default=None
    )
    name: str = Field(sa_column=Column(String(100), nullable=False))
    price: float = Field(sa_column=Column(BIGINT, nullable=False))
    created_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={"server_default": func.current_timestamp()},
    )

    @property
    def formatted_price(self) -> str:
        return f"${self.price:.2f}" if self.price else "Free"
