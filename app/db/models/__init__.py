"""QuickBite AI + Loyalty — Database Models Package.

All 19 tables from Doc 2 v3.0, organised into the 4 domain schemas:
restaurant, customer, payment, static.
"""

from app.db.models.audit import AuditLog
from app.db.models.branch import Branch
from app.db.models.customer import Customer
from app.db.models.identity_link import IdentityLink
from app.db.models.loyalty import RewardProgram, StampLog
from app.db.models.outbox import ProjectionOutbox
from app.db.models.payment import BillingAuditLog, BillingEvent, Invoice, PaymentMethod
from app.db.models.reputation import CustomerReview, GMBProfile, ReviewResponse
from app.db.models.static_data import FeatureFlag, NotificationTemplate
from app.db.models.subscription import Subscription, SubscriptionPlan, UsageTracking
from app.db.models.tenant import Tenant
from app.db.models.user import Role, Session, User

__all__ = [
    "AuditLog",
    "BillingAuditLog",
    "BillingEvent",
    "Branch",
    "Customer",
    "CustomerReview",
    "FeatureFlag",
    "GMBProfile",
    "IdentityLink",
    "Invoice",
    "NotificationTemplate",
    "PaymentMethod",
    "ProjectionOutbox",
    "ReviewResponse",
    "RewardProgram",
    "Role",
    "Session",
    "StampLog",
    "Subscription",
    "SubscriptionPlan",
    "Tenant",
    "UsageTracking",
    "User",
]
