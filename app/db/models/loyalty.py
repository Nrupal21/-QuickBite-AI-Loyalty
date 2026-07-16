"""QuickBite — RewardProgram, StampLog models (Doc 2 Tables 12/13).

RewardProgram is owner configuration (restaurant schema). StampLog is
append-only scan history with GPS at scan time for fraud audit (customer
schema — it references loyalty members).

ScratchCard belongs to LOYALTY-04 and is not built yet.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# TODO(LOYALTY-04): ScratchCard model.


class RewardProgram(Base):
    """Owner-configured loyalty rules per branch."""

    __tablename__ = "reward_programs"
    __table_args__ = {"schema": "restaurant"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.branches.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    stamps_required: Mapped[int] = mapped_column(Integer)
    reward_type: Mapped[str] = mapped_column(String)  # free_item / percentage_off / fixed_off
    reward_value: Mapped[str] = mapped_column(String)
    validity_days: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class StampLog(Base):
    """Every QR loyalty scan. Append-only. GPS at scan time for fraud audit."""

    __tablename__ = "stamp_logs"
    __table_args__ = {"schema": "customer"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    branch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.branches.id"), index=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customer.customers.id"), nullable=True, index=True
    )
    customer_phone_hash: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    gps_latitude_at_scan: Mapped[float] = mapped_column(Float)
    gps_longitude_at_scan: Mapped[float] = mapped_column(Float)
    distance_from_branch_m: Mapped[float] = mapped_column(Float)
    is_fraudulent: Mapped[bool] = mapped_column(Boolean, default=False)
    scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
