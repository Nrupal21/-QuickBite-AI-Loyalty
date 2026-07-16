---
trigger: model_decision
description: Activate when writing DB models, queries, migrations, or PostGIS geofence code
---

# QuickBite — Database Rules

## SQLAlchemy 2.x Async — Required Patterns
```python
# ✅ Correct async pattern
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

async def get_customer_by_phone_hash(
    session: AsyncSession, phone_hash: str
) -> Customer | None:
    result = await session.execute(
        select(Customer).where(Customer.phone_hash == phone_hash)
    )
    return result.scalar_one_or_none()

# ❌ Never use sync ORM in async context
session.query(Customer).filter(...)  # BANNED in async FastAPI
```

## Model Base Class
```python
# All models inherit from Base (defined in app/db/base.py)
class StampLog(Base):
    __tablename__ = "stamp_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    # tenant_id is always required — RLS filters by this
```

## PostGIS Geofence Pattern
```python
from geoalchemy2 import Geography
from geoalchemy2.functions import ST_DWithin
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

# Store branch location
class Branch(Base):
    location: Mapped[Any] = mapped_column(Geography(geometry_type="POINT", srid=4326))

# Geofence query — always use Geography type for metre-based distance
customer_point = from_shape(Point(gps_lng, gps_lat), srid=4326)
stmt = select(Branch).where(
    Branch.tenant_id == tenant_id,
    Branch.qr_code_token == qr_token,
    ST_DWithin(
        Branch.location.cast(Geography),
        func.ST_GeographyFromText(f"POINT({gps_lng} {gps_lat})"),
        Branch.geofence_radius_m
    )
)
# ST_DWithin with Geography type measures distance in metres (not degrees)
```

## Migrations — Rules
- Every schema change = new Alembic revision file
- RLS migrations must include both: `ENABLE ROW LEVEL SECURITY` and `CREATE POLICY`
- PostGIS columns: use `AddGeometryColumn` or `Geography` type via `op.execute()`
- Never edit an existing migration — always create a new one

## 15 Tables with RLS
All of these have RLS enabled. Always include `tenant_id` in queries:
users, roles, user_roles, sessions, branches, subscription_plans, subscriptions,
usage_tracking, google_business_profiles, customer_reviews, review_responses,
reward_programs, stamp_logs, scratch_cards, customers, audit_logs
