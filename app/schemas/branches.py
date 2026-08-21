"""QuickBite — Branch schemas: GET /branches (DASH-03, read-only list).

Powers the Google Profile Link and Settings dashboard pages. Branch
create/update/delete is a separate, later ticket — this file only carries the
list-read shape both of those pages need today.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class BranchOut(BaseModel):
    """One `restaurant.branches` row, joined with its `google_business_profiles`
    connection state (if any) — the Google Profile Link page's one query.

    `qr_code_token` is not a secret: it is printed on the branch's receipts
    for any diner to scan (loyalty_service._get_active_branch,
    review_service's equivalent lookup), so returning it to any Manager+
    caller who can already see the branch discloses nothing new — it is what
    the QR Codes page's manual-entry fallback and regenerate confirmation
    display.
    """

    id: uuid.UUID
    name: str
    is_active: bool
    qr_code_token: str
    gmb_connected: bool
    gmb_last_synced_at: datetime | None
