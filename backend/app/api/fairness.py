from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require
from app.core.permissions import Permission
from app.database.connection import get_db
from app.database.models import FairnessFinding, FairnessRun, User
from app.services.audit_service import record_event
from app.services.fairness_service import impact_ratio

router = APIRouter(prefix="/api/fairness", tags=["Fairness (admin)"])


class RunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str = "resume_shortlist"
    groups: dict[str, tuple[int, int]] = {}


@router.post("/runs")
def start_run(
    body: RunBody, db: Session = Depends(get_db), user: User = Depends(require(Permission.VIEW_FAIRNESS))
):
    ratios = impact_ratio(body.groups)
    run = FairnessRun(created_by=user.user_id, stage=body.stage, metrics={"impact_ratios": ratios})
    db.add(run)
    db.flush()
    for cohort, ratio in ratios.items():
        if ratio is not None and ratio < 0.8:
            db.add(
                FairnessFinding(
                    run_id=run.run_id,
                    severity="warning",
                    metric="impact_ratio",
                    cohort=cohort,
                    value=ratio,
                    threshold=0.8,
                )
            )
    record_event(
        db,
        action="fairness.run",
        actor_id=user.user_id,
        entity_type="fairness_run",
        entity_id=str(run.run_id),
        details={"stage": body.stage},
    )
    db.commit()
    return {"run_id": str(run.run_id), "metrics": run.metrics}


@router.get("/runs")
def runs(db: Session = Depends(get_db), user: User = Depends(require(Permission.VIEW_FAIRNESS))):
    return {
        "items": [
            {"run_id": str(item.run_id), "stage": item.stage, "metrics": item.metrics}
            for item in db.scalars(select(FairnessRun).order_by(FairnessRun.created_at.desc())).all()
        ]
    }


@router.get("/findings")
def findings(db: Session = Depends(get_db), user: User = Depends(require(Permission.VIEW_FAIRNESS))):
    rows = db.scalars(select(FairnessFinding)).all()
    return {
        "items": [
            {
                "finding_id": str(item.finding_id),
                "metric": item.metric,
                "cohort": item.cohort,
                "value": item.value,
                "threshold": item.threshold,
                "severity": item.severity,
            }
            for item in rows
        ]
    }
