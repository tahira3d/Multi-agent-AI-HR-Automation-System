"""Database side of the audit trail. Events are only ever appended."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database.models import AuditEvent, AuditMutex
from app.services.audit_chain import GENESIS_HASH, compute_hash, verify_chain

# Keys that must never be written into audit details (privacy: no resume text / contact data).
_FORBIDDEN_DETAIL_KEYS = {"extracted_text", "resume_text", "transcript", "password", "email", "phone"}


def _clean_details(details: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (details or {}).items() if k.lower() not in _FORBIDDEN_DETAIL_KEYS}


def record_event(
    db: Session,
    *,
    action: str,
    actor_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one event to the chain. Caller commits (so the event shares the business transaction)."""
    db.flush()  # Acquire pending business-row locks before the global audit mutex.
    if db.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.execute(insert(AuditMutex).values(lock_id=1, revision=0).on_conflict_do_nothing(index_elements=["lock_id"]))
    db.execute(update(AuditMutex).where(AuditMutex.lock_id == 1).values(revision=AuditMutex.revision + 1))
    last = db.execute(select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1).execution_options(populate_existing=True)).scalar_one_or_none()
    seq = (last.seq + 1) if last else 1
    prev_hash = last.hash if last else GENESIS_HASH
    payload = dict(
        seq=seq,
        application_id=str(application_id) if application_id else None,
        actor_id=str(actor_id) if actor_id else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=_clean_details(details),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    event = AuditEvent(
        seq=seq,
        application_id=application_id,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=payload["details"],
        created_at=payload["created_at"],
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, payload),
    )
    db.add(event)
    db.flush()
    return event


def _as_payload(e: AuditEvent) -> dict[str, Any]:
    return dict(
        seq=e.seq,
        application_id=str(e.application_id) if e.application_id else None,
        actor_id=str(e.actor_id) if e.actor_id else None,
        action=e.action,
        entity_type=e.entity_type,
        entity_id=e.entity_id,
        details=e.details,
        created_at=e.created_at,
        prev_hash=e.prev_hash,
        hash=e.hash,
    )


def verify_stored_chain(db: Session, expected_head: str | None = None) -> dict[str, Any]:
    events = db.execute(select(AuditEvent).order_by(AuditEvent.seq)).scalars().all()
    ok, bad_index = verify_chain((_as_payload(e) for e in events), expected_head=expected_head)
    return {
        "intact": ok,
        "events": len(events),
        "first_bad_seq": events[bad_index].seq if (not ok and bad_index is not None and bad_index < len(events)) else None,
        "head_hash": events[-1].hash if events else GENESIS_HASH,
    }
