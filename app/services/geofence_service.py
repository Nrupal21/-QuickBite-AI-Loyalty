"""QuickBite — PostGIS ST_Distance geofence validation."""

from geoalchemy2 import Geography
from geoalchemy2.functions import ST_Distance
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.branch import Branch


async def distance_to_branch_m(session: AsyncSession, branch: Branch, lat: float, lng: float) -> float:
    """Distance in metres between (lat, lng) and the branch location, via PostGIS geography cast."""
    customer_point = from_shape(Point(lng, lat), srid=4326)
    result = await session.execute(
        select(
            ST_Distance(
                cast(Branch.location, Geography),
                cast(customer_point, Geography),
            )
        ).where(Branch.id == branch.id)
    )
    return result.scalar_one()


def is_within_geofence(distance_m: float, radius_m: int) -> bool:
    return distance_m <= radius_m
