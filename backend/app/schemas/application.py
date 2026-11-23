from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UploadItemResult(BaseModel):
    filename: str
    outcome: str  # accepted | needs_manual_review | rejected | duplicate
    application_id: UUID | None = None
    reason_code: str | None = None
    message: str | None = None
    warnings: list[str] = []
    duplicate_of_application_id: UUID | None = None


class UploadResponse(BaseModel):
    requisition_id: UUID
    results: list[UploadItemResult]
    accepted: int
    needs_manual_review: int
    rejected: int
    duplicates: int


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    application_id: UUID
    requisition_id: UUID
    candidate_id: UUID
    candidate_name: str
    email: str | None = None
    current_status: str
    parse_status: str | None = None
    warnings: list[str] = []
    created_at: datetime


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    action: str
    actor_id: UUID | None
    entity_type: str | None
    entity_id: str | None
    details: dict
    created_at: str
    hash: str
    decision: str | None = None
    reason: str | None = None
    reviewed_by: UUID | None = None
    reviewed_at: datetime | None = None
