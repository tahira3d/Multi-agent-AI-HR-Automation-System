"""Explainable evaluations, ranked review and authorized shortlist decisions."""
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.permissions import Permission
from app.database.connection import get_db
from app.schemas.evaluation import ApprovalDecisionOut, EvaluationOut, EvaluationRunOut, RankingsOut
from app.services.evaluation_service import EvaluationFailure, EvaluationService, evaluation_dict

router = APIRouter(prefix="/api", tags=["Evaluations and HR review"])


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    force_reevaluation: bool = False
    expected_rubric_version: int | None = Field(default=None, ge=1)


class ShortlistDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_id: UUID
    decision: Literal["approve", "decline", "hold"]
    reason: str | None = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def require_decision_reason(self):
        self.reason = (self.reason or "").strip()
        if self.decision in {"decline", "hold"} and not self.reason:
            raise ValueError("A reason is required when declining or holding an application.")
        return self


@router.post("/applications/{application_id}/evaluate", response_model=EvaluationRunOut, summary="Evaluate a resume using its approved job rubric")
def evaluate_application(application_id: UUID, body: EvaluateRequest | None = None,
                         db: Session = Depends(get_db), user=Depends(require(Permission.EVALUATE_RESUMES))):
    try:
        return EvaluationService(db, user).evaluate(application_id, **(body or EvaluateRequest()).model_dump())
    except EvaluationFailure as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@router.get("/applications/{application_id}/evaluation", response_model=EvaluationOut, summary="Read current or historical scoring details")
def get_evaluation(application_id: UUID, version: int | None = Query(None, ge=1),
                   db: Session = Depends(get_db), user=Depends(require(Permission.VIEW_CANDIDATES))):
    return evaluation_dict(EvaluationService(db, user).latest_evaluation(application_id, version))


@router.get("/requisitions/{requisition_id}/rankings", response_model=RankingsOut, summary="Ranked review queue; scores never decide progression")
def get_rankings(requisition_id: UUID, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                 status: str | None = Query(None, max_length=80), db: Session = Depends(get_db),
                 user=Depends(require(Permission.VIEW_CANDIDATES))):
    return EvaluationService(db, user).rankings(requisition_id, offset=offset, limit=limit, status=status)


@router.post("/applications/{application_id}/shortlist-decision", response_model=ApprovalDecisionOut, summary="Resume an approval checkpoint using the HR decision")
def shortlist_decision(application_id: UUID, body: ShortlistDecisionRequest, db: Session = Depends(get_db),
                       user=Depends(require(Permission.APPROVE_SHORTLIST))):
    try:
        return EvaluationService(db, user).decide(application_id, **body.model_dump())
    except EvaluationFailure as exc:
        raise HTTPException(exc.status_code, exc.message) from exc
