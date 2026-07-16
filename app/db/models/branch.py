"""QuickBite — Branch model (Doc 2 Table 5, restaurant schema).

One row per restaurant location. v3.1 changes:
- address stored TIER 3 (address_hash + encrypted_address) — plaintext
  address column removed.
- GPS stored ONLY in the PostGIS `location` column — plaintext
  latitude/longitude columns removed (eliminates a coordinate leak vector).
"""

import uuid

from geoalchemy2 import Geometry
from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = {"schema": "restaurant"}

    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("restaurant.tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    # v3.1 TIER 3 — hash of normalised address for lookup + AES-256-GCM for display
    address_hash: Mapped[str] = mapped_column(String, index=True)
    encrypted_address: Mapped[str] = mapped_column(String)
    location: Mapped[str] = mapped_column(Geometry(geometry_type="POINT", srid=4326))
    geofence_radius_m: Mapped[int] = mapped_column(Integer, default=100)
    qr_code_token: Mapped[str] = mapped_column(String, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
