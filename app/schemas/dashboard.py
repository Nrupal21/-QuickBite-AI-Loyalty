"""QuickBite — Dashboard schemas: GET /dashboard/stats (DASH-01),
GET /loyalty/analytics (DASH-02)."""

import uuid
from datetime import datetime

from pydantic import BaseModel


class SentimentTrendPoint(BaseModel):
    date: str  # ISO date, one point per day
    avg_sentiment: float | None  # None when no reviews landed that day


class DashboardStatsResponse(BaseModel):
    avg_rating: float | None
    review_count: int
    sentiment_trend: list[SentimentTrendPoint]
    pending_approvals: int
    loyalty_scans_today: int
    fraud_alert_count: int
    # False for STAFF (Doc 5 DASH-01: "Staff role: read-only response") —
    # OWNER/MANAGER/SUPER_ADMIN get True. The dashboard UI (STITCH-07) reads
    # this to decide whether to render Approve/Reject buttons at all, rather
    # than rendering them and relying on the route guard to reject the click.
    can_approve: bool


class HeatmapPoint(BaseModel):
    branch_id: uuid.UUID
    hour: int  # 0-23, local to the DB's stored UTC timestamp — see dashboard_service note
    scan_count: int


class TopCustomer(BaseModel):
    customer_id: uuid.UUID
    phone_masked: str  # "*** **** XXXX" — never the full number
    total_stamps: int


class BranchComparisonPoint(BaseModel):
    branch_id: uuid.UUID
    branch_name: str
    scan_count: int


class FraudLogEntry(BaseModel):
    scanned_at: datetime
    branch_id: uuid.UUID
    distance_from_branch_m: float
    reason: str


class LoyaltyAnalyticsResponse(BaseModel):
    total_stamps_month: int  # valid (non-fraudulent) scans in the last 30 days
    active_loyalty_customers: int  # distinct customers who scanned in the last 30 days
    heatmap: list[HeatmapPoint]
    top_customers: list[TopCustomer]
    branch_comparison: list[BranchComparisonPoint]
    redemption_rate: float  # redeemed / generated, 0.0 when none generated yet
    fraud_log: list[FraudLogEntry]
