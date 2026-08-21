"""QuickBite — Role definitions + permission matrix.

Defines RBAC roles: Super Admin, Owner, Manager, Staff, Customer.
Maps each role to allowed API operations.

Role.level (static.roles, seeded) is the numeric rank `require_role()`
compares against — lower number = more senior. RoleLevel mirrors those
seeded values so route declarations read as `require_role(RoleLevel.MANAGER)`
instead of a bare, unexplained integer.
"""


class RoleLevel:
    SUPER_ADMIN = 1
    OWNER = 2
    MANAGER = 3
    STAFF = 4
    # Loyalty customers never hold a static.roles row — they authenticate
    # against customer.customers with CUSTOMER_SECRET_KEY and are guarded by
    # require_customer_session(), not require_role(). The level exists only so
    # Doc 3's matrix has a complete ranking; never pass it to require_role().
    CUSTOMER = 5
    # Standard, tenant-less accounts: everyone who has verified their email but
    # has not yet registered a restaurant (User.tenant_id IS NULL). Below every
    # staff rank on purpose — require_role(RoleLevel.STAFF) and above already
    # 403 a USER caller with no extra code, since every existing call site
    # passes a level strictly less than this one.
    USER = 6


# Roles an Owner may hand out via /team/invite. SUPER_ADMIN and OWNER are
# deliberately absent: an Owner inviting either would be lateral or vertical
# privilege escalation, and Doc 3 grants "invite/remove team members" to
# OWNER solely for the Manager and Staff ranks below them.
INVITABLE_ROLE_NAMES = ("MANAGER", "STAFF")
