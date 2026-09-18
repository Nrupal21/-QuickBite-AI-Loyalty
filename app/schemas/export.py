"""QuickBite — Analytics export schemas (NICE-04)."""

from pydantic import BaseModel


class ExportQueuedResponse(BaseModel):
    status: str  # always "queued"
    format: str  # "csv" | "pdf"
    export_id: str
