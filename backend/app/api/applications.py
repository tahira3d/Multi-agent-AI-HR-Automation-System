from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import accessible_application, require, requisition_scope
from app.core.permissions import Permission
from app.core.status import Status
from app.core.config import get_settings
from app.database.connection import get_db
from app.database.models import Application, Approval, AuditEvent, Evaluation, Requisition, ResumeFile, User
from app.schemas.application import AuditEventOut
from app.services.audit_service import verify_stored_chain

router = APIRouter(prefix="/api", tags=["Application activity"])


@router.get("/applications/{application_id}/history", response_model=list[AuditEventOut])
def application_history(application_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                        db: Session = Depends(get_db), user: User = Depends(require(Permission.VIEW_AUDIT))):
    accessible_application(db, application_id, user)
    events = db.scalars(select(AuditEvent).where(AuditEvent.application_id == application_id)
                       .order_by(AuditEvent.seq).offset(offset).limit(limit)).all()
    decisions = {str(value.approval_id): value for value in db.scalars(
        select(Approval).where(Approval.application_id == application_id)).all()}
    result = []
    for event in events:
        item = AuditEventOut.model_validate(event)
        decision = decisions.get((event.details or {}).get("approval_id"))
        if decision:
            # Reasons remain in deletable approval records rather than the immutable hash payload.
            item.decision, item.reason = decision.decision, decision.reason
            item.reviewed_by, item.reviewed_at = decision.reviewed_by, decision.reviewed_at
        result.append(item)
    return result


@router.get("/audit/verify")
def verify_audit_chain(expected_head: str | None = None, db: Session = Depends(get_db),
                       user: User = Depends(require(Permission.VIEW_AUDIT))):
    # Returns aggregate integrity, never events or candidate details.
    return verify_stored_chain(db, expected_head=expected_head)


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(require(Permission.VIEW_CANDIDATES))):
    requisitions = select(Requisition.requisition_id).where(requisition_scope(user))
    applications = select(Application.application_id).where(Application.requisition_id.in_(requisitions))
    def count(model, *conditions):
        return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0
    conditions = [Application.requisition_id.in_(requisitions)]
    activities = db.scalars(select(AuditEvent).where(or_(
        AuditEvent.application_id.in_(applications),
        (AuditEvent.entity_type == "requisition") & AuditEvent.entity_id.in_(
            [str(item) for item in db.scalars(requisitions).all()]),
        AuditEvent.actor_id == user.user_id,
    )).order_by(AuditEvent.seq.desc()).limit(20)).all()
    return {
        "evaluation_available": get_settings().groq_configured,
        "total_requisitions": count(Requisition, requisition_scope(user)),
        "total_resumes": count(ResumeFile, ResumeFile.application_id.in_(applications)),
        "total_evaluated": count(Evaluation, Evaluation.application_id.in_(applications), Evaluation.is_active.is_(True)),
        "awaiting_review": count(Application, *conditions, Application.current_status == Status.SCORED_HR_REVIEW),
        "approved_for_screening": count(Application, *conditions, Application.current_status == Status.APPROVED_FOR_SCREENING),
        "recent_activity": [{"action": e.action, "application_id": str(e.application_id) if e.application_id else None,
                             "actor_id": str(e.actor_id) if e.actor_id else None, "created_at": e.created_at}
                            for e in activities],
    }


@router.delete("/applications/{application_id}", status_code=204)
def delete_application(application_id: UUID, require_expired: bool = True, db: Session = Depends(get_db),
                       user: User = Depends(require(Permission.DELETE_CANDIDATE_DATA))):
    from app.services.retention import purge_application
    application = accessible_application(db, application_id, user)
    purge_application(db, application, user, require_expired=require_expired)
