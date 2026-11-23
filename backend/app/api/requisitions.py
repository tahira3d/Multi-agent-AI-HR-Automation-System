"""Requisition CRUD with ownership, immutable rubric snapshots and optimistic locking."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import accessible_requisition, require, requisition_scope
from app.core.permissions import Permission
from app.database.connection import get_db
from app.database.models import Application, Candidate, Requisition, ResumeFile, RubricVersion, User
from app.schemas.application import ApplicationOut
from app.schemas.requisition import RequisitionCreate, RequisitionOut, RequisitionUpdate
from app.services.audit_service import record_event

router = APIRouter(prefix="/api/requisitions", tags=["Requisitions"])
JOB_FIELDS = tuple(RequisitionCreate.model_fields)


def snapshot(req: Requisition, user: User) -> RubricVersion:
    return RubricVersion(requisition_id=req.requisition_id, version=req.rubric_version,
                        rubric=req.rubric, approved_by=user.user_id,
                        job_snapshot={key: getattr(req, key) for key in JOB_FIELDS})


@router.post("", response_model=RequisitionOut, status_code=201)
def create_requisition(body: RequisitionCreate, db: Session = Depends(get_db),
                       user: User = Depends(require(Permission.MANAGE_REQUISITIONS))):
    req = Requisition(created_by=user.user_id, **body.model_dump())
    db.add(req)
    db.flush()
    db.add(snapshot(req, user))
    record_event(db, action="requisition_created", actor_id=user.user_id, entity_type="requisition",
                 entity_id=str(req.requisition_id), details={"rubric_version": req.rubric_version})
    db.commit()
    return req


@router.get("", response_model=list[RequisitionOut])
def list_requisitions(offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                      db: Session = Depends(get_db), user: User = Depends(require(Permission.VIEW_CANDIDATES))):
    return db.scalars(select(Requisition).where(requisition_scope(user))
                      .order_by(Requisition.created_at.desc()).offset(offset).limit(limit)).all()


@router.get("/{requisition_id}", response_model=RequisitionOut)
def get_requisition(requisition_id: UUID, db: Session = Depends(get_db),
                    user: User = Depends(require(Permission.VIEW_CANDIDATES))):
    return accessible_requisition(db, requisition_id, user)


@router.patch("/{requisition_id}", response_model=RequisitionOut)
def edit_requisition(requisition_id: UUID, body: RequisitionUpdate, db: Session = Depends(get_db),
                     user: User = Depends(require(Permission.MANAGE_REQUISITIONS))):
    req = accessible_requisition(db, requisition_id, user)
    old_version = req.rubric_version
    if body.expected_rubric_version is not None and body.expected_rubric_version != old_version:
        raise HTTPException(409, "The requisition changed. Refresh before saving.")
    changes = body.model_dump(exclude_unset=True, exclude={"expected_rubric_version"})
    merged = {key: getattr(req, key) for key in JOB_FIELDS}
    merged.update(changes)
    try:
        data = RequisitionCreate.model_validate(merged).model_dump()
    except ValidationError as exc:
        raise HTTPException(422, [{"loc": item["loc"], "msg": item["msg"]} for item in exc.errors()]) from None
    if all(getattr(req, key) == value for key, value in data.items()):
        return req
    changed = db.execute(update(Requisition).where(
        Requisition.requisition_id == req.requisition_id, Requisition.rubric_version == old_version
    ).values(**data, rubric_version=old_version + 1), execution_options={"synchronize_session": False}).rowcount
    if changed != 1:
        db.rollback()
        raise HTTPException(409, "The requisition changed. Refresh before saving.")
    db.expire(req)
    db.add(snapshot(req, user))
    record_event(db, action="requisition_updated", actor_id=user.user_id, entity_type="requisition",
                 entity_id=str(req.requisition_id), details={"rubric_version": req.rubric_version})
    db.commit()
    return req


@router.get("/{requisition_id}/applications", response_model=list[ApplicationOut])
def list_applications(requisition_id: UUID, offset: int = Query(0, ge=0),
                      limit: int = Query(50, ge=1, le=100), db: Session = Depends(get_db),
                      user: User = Depends(require(Permission.VIEW_CANDIDATES))):
    accessible_requisition(db, requisition_id, user)
    rows = db.execute(select(Application, Candidate, ResumeFile)
                      .join(Candidate, Candidate.candidate_id == Application.candidate_id)
                      .join(ResumeFile, ResumeFile.application_id == Application.application_id)
                      .where(Application.requisition_id == requisition_id)
                      .order_by(Application.created_at).offset(offset).limit(limit)).all()
    return [ApplicationOut(application_id=a.application_id, requisition_id=a.requisition_id,
            candidate_id=a.candidate_id, candidate_name=c.full_name, email=c.email,
            current_status=a.current_status, parse_status=f.parse_status,
            warnings=f.warnings or [], created_at=a.created_at) for a, c, f in rows]
