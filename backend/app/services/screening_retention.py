"""Explicit removal of screening content and its durable checkpoints."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, func, select
from app.core.config import get_settings
from app.core.screening_config import get_voice_settings
from app.database.models import Screening
from app.database.screening_models import ScreeningAnswer, ScreeningCommand, ScreeningQuestion, ScreeningReport, ScreeningSession
from app.workflows.hiring_graph import persistent_checkpointer


def delete_screening_content(db, session):
    identity = session.screening_session_id
    with persistent_checkpointer(get_settings()) as saver:
        saver.delete_thread(f'screening:{identity}')
    for model in (ScreeningCommand, ScreeningReport, ScreeningAnswer, ScreeningQuestion):
        db.execute(delete(model).where(model.screening_session_id == identity))
    legacy = db.get(Screening, session.screening_id)
    if legacy:
        legacy.answers, legacy.summary = [], None
    db.delete(session)
    db.flush()


def purge_application_screenings(db, application_id):
    for session in db.scalars(select(ScreeningSession).where(ScreeningSession.application_id == application_id)).all():
        if session.lease_expires_at:
            expiry = session.lease_expires_at
            expiry = expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry
            if expiry > datetime.now(timezone.utc):
                from fastapi import HTTPException
                raise HTTPException(409, 'A screening operation is active. Stop it or wait before deleting the application.')
        delete_screening_content(db, session)


def expired_screenings(db):
    cutoff = datetime.now(timezone.utc) - timedelta(days=get_voice_settings().screening_retention_days)
    return list(db.scalars(select(ScreeningSession).where(func.coalesce(ScreeningSession.ended_at, ScreeningSession.started_at, ScreeningSession.created_at) < cutoff,
        ScreeningSession.status.not_in(['transcribing', 'preparing_followup']))))


def purge_expired_content():
    from app.database.connection import SessionLocal
    from app.services.audit_service import record_event
    with SessionLocal() as db:
        for session in expired_screenings(db):
            identity, application_id = session.screening_session_id, session.application_id
            delete_screening_content(db, session)
            record_event(db, action='screening.retention_removed', application_id=application_id,
                entity_type='screening', entity_id=str(identity), details={'content_removed': True})
        db.commit()


async def retention_worker():
    import asyncio
    import logging
    while True:
        try:
            if get_voice_settings().screening_retention_automatic:
                await asyncio.to_thread(purge_expired_content)
        except Exception:
            logging.getLogger(__name__).warning('Screening retention cleanup deferred; storage/configuration must recover.')
        await asyncio.sleep(3600)
