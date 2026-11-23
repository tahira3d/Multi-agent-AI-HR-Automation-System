"""Explicit deletion of one application and its private data/checkpoints."""
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, select
from app.core.config import get_settings
from app.core.status import Status
from app.database.models import Application, Approval, Candidate, Communication, Evaluation, Interview, ResumeFile, Screening
from app.services.audit_service import record_event
from app.workflows.hiring_graph import delete_workflow_checkpoints


def purge_application(db, application, user, *, require_expired=True):
    settings = get_settings()
    if application.processing_token:
        raise HTTPException(409, "The application is being processed")
    timestamp = application.updated_at
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    expired = timestamp < datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    if require_expired and (application.current_status != Status.CLOSED or not expired):
        raise HTTPException(409, "Automatic retention deletion requires a closed application older than the configured retention period")
    app_id, candidate_id = application.application_id, application.candidate_id
    from app.services.screening_retention import purge_application_screenings
    purge_application_screenings(db, app_id)
    files = db.scalars(select(ResumeFile).where(ResumeFile.application_id == app_id)).all()
    root = settings.storage_dir.resolve()
    paths = [(root / f.storage_key).resolve() for f in files]
    if any(not path.is_relative_to(root) for path in paths):
        raise HTTPException(409, "Invalid private storage reference; administrator review is required")
    # Retain no evidence in durable graph history after an authorized deletion.
    delete_workflow_checkpoints(settings, str(app_id), application.evaluation_version)
    for path in paths:
        path.unlink(missing_ok=True)
    for model in (Approval, Evaluation, ResumeFile, Communication, Interview, Screening):
        db.execute(delete(model).where(model.application_id == app_id))
    db.delete(application)
    db.flush()
    if db.scalar(select(Application).where(Application.candidate_id == candidate_id).limit(1)) is None:
        db.execute(delete(Candidate).where(Candidate.candidate_id == candidate_id))
    # The audit trail keeps pseudonymous UUIDs, no names, contact details or resume text.
    record_event(db, action="candidate_data_deleted", actor_id=user.user_id, application_id=app_id,
                 details={"retention_days": settings.retention_days})
    db.commit()
