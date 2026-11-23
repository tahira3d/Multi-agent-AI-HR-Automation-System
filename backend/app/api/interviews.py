"""Phase 4 interview evaluation, report download, and human gates."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_application, require
from app.core.permissions import Permission
from app.core.status import Status, assert_transition
from app.database.connection import get_db
from app.database.models import (
    Approval,
    GeneratedReport,
    InterviewEvaluation,
    InterviewScoreOverride,
    User,
)
from app.services.audit_service import record_event
from app.services.interview_scoring_service import as_dict, evaluate
from app.services.report_service import MIME, get_or_create

router = APIRouter(prefix="/api", tags=["Interview scoring and reports"])


class OverrideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criterion: str = Field(min_length=1, max_length=120)
    new_level: int = Field(ge=0, le=4)
    reason: str = Field(min_length=3, max_length=2000)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str
    reason: str = Field(min_length=3, max_length=2000)


def _current(db: Session, application_id: UUID) -> InterviewEvaluation:
    result = db.scalar(
        select(InterviewEvaluation).where(
            InterviewEvaluation.application_id == application_id, InterviewEvaluation.is_current.is_(True)
        )
    )
    if result is None:
        raise HTTPException(404, "No current interview evaluation exists.")
    return result


@router.post("/applications/{application_id}/interview-evaluate")
def interview_evaluate(
    application_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require(Permission.APPROVE_SCREENING)),
):
    application = accessible_application(db, application_id, user)
    # The legacy Phase 3 route set Approved for Interview at screening approval;
    # retain compatibility while new flow normally begins from screening review.
    if application.current_status not in {
        Status.SCREENING_COMPLETED_HR_REVIEW,
        Status.APPROVED_FOR_INTERVIEW,
    }:
        raise HTTPException(409, "Interview evaluation requires an HR-reviewed completed screening.")
    return as_dict(evaluate(db, application, user))


@router.get("/applications/{application_id}/interview-evaluation")
def interview_evaluation(
    application_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require(Permission.VIEW_CANDIDATES)),
):
    accessible_application(db, application_id, user)
    return as_dict(_current(db, application_id))


@router.post("/applications/{application_id}/interview-score-review")
def interview_score_review(
    application_id: UUID,
    body: OverrideBody,
    db: Session = Depends(get_db),
    user: User = Depends(require(Permission.APPROVE_SCREENING)),
):
    application = accessible_application(db, application_id, user)
    evaluation = _current(db, application_id)
    result = next(
        (item for item in evaluation.criterion_results if item.get("criterion") == body.criterion), None
    )
    if result is None or result.get("anchor_level") is None:
        raise HTTPException(422, "Only a scored criterion can be overridden.")
    override = InterviewScoreOverride(
        evaluation_id=evaluation.evaluation_id,
        criterion=body.criterion,
        original_level=int(result["anchor_level"]),
        new_level=body.new_level,
        reason=body.reason,
        reviewer_id=user.user_id,
    )
    db.add(override)
    record_event(
        db,
        action="interview.score_overridden",
        actor_id=user.user_id,
        application_id=application.application_id,
        entity_type="interview_evaluation",
        entity_id=str(evaluation.evaluation_id),
        details={"criterion": body.criterion},
    )
    db.commit()
    return {"override_id": str(override.override_id), "original_ai_score_preserved": True}


@router.get("/applications/{application_id}/interview-report")
def interview_report(
    application_id: UUID,
    format: str = "pdf",
    db: Session = Depends(get_db),
    user: User = Depends(require(Permission.VIEW_CANDIDATES)),
):
    if format not in MIME:
        raise HTTPException(422, "format must be pdf, docx, or json")
    application = accessible_application(db, application_id, user)
    report = get_or_create(db, _current(db, application_id), format, user)
    record_event(
        db,
        action="report.downloaded",
        actor_id=user.user_id,
        application_id=application.application_id,
        entity_type="report",
        entity_id=str(report.report_id),
        details={"format": format},
    )
    db.commit()
    return FileResponse(
        Path(report.storage_reference),
        media_type=MIME[format],
        filename=f"report-{report.report_id}.{format}",
    )


@router.get("/applications/{application_id}/interview-reports")
def interview_reports(
    application_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require(Permission.VIEW_CANDIDATES)),
):
    accessible_application(db, application_id, user)
    evaluation = _current(db, application_id)
    rows = db.scalars(
        select(GeneratedReport).where(GeneratedReport.evaluation_id == evaluation.evaluation_id)
    ).all()
    return {
        "items": [
            {
                "report_id": str(row.report_id),
                "format": row.format,
                "version": row.version,
                "sha256": row.sha256,
            }
            for row in rows
        ]
    }


@router.post("/applications/{application_id}/final-decision")
def final_decision(
    application_id: UUID,
    body: DecisionBody,
    db: Session = Depends(get_db),
    user: User = Depends(require(Permission.APPROVE_SCREENING)),
):
    application = accessible_application(db, application_id, user)
    if application.current_status != Status.INTERVIEW_SCORED_HR_REVIEW:
        raise HTTPException(409, "Final decision requires the interview-score review state.")
    target = {"approve": Status.APPROVED_FOR_INTERVIEW, "hold": Status.ON_HOLD, "close": Status.CLOSED}.get(
        body.decision
    )
    if target is None:
        raise HTTPException(422, "decision must be approve, hold, or close")
    if db.scalar(
        select(Approval).where(Approval.application_id == application_id, Approval.approval_type == "final")
    ):
        raise HTTPException(409, "A final decision already exists for this cycle.")
    assert_transition(application.current_status, target)
    application.current_status = target
    approval = Approval(
        application_id=application_id,
        reviewed_by=user.user_id,
        approval_type="final",
        decision=body.decision,
        reason=body.reason,
    )
    db.add(approval)
    record_event(
        db,
        action="interview.final_decision",
        actor_id=user.user_id,
        application_id=application_id,
        entity_type="approval",
        entity_id=str(approval.approval_id),
        details={"decision": body.decision},
    )
    db.commit()
    return {"status": application.current_status, "approval_id": str(approval.approval_id)}
