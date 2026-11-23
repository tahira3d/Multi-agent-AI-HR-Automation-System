"""Candidate reads expose only applications visible to the authenticated recruiter."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import require, requisition_scope
from app.core.permissions import Permission
from app.database.connection import get_db
from app.database.models import Application, Candidate, Requisition

router = APIRouter(prefix="/api/candidates", tags=["Candidates"])


@router.get("/{candidate_id}")
def candidate_details(candidate_id: UUID, db: Session = Depends(get_db),
                      user=Depends(require(Permission.VIEW_CANDIDATES))):
    applications = db.scalars(select(Application).join(Requisition).where(
        Application.candidate_id == candidate_id, requisition_scope(user))).all()
    if not applications:
        raise HTTPException(404, "Candidate not found")
    candidate = db.get(Candidate, candidate_id)
    return {"candidate_id": str(candidate.candidate_id), "full_name": candidate.full_name,
            "email": candidate.email, "phone": candidate.phone,
            "applications": [{"application_id": str(a.application_id), "requisition_id": str(a.requisition_id),
                              "current_status": a.current_status} for a in applications]}
