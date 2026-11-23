"""Recruiter-only Agent 3 decision-support API."""
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.api.deps import require
from app.core.permissions import Permission
from app.database.connection import get_db
from app.database.models import User
from app.services.final_recommendation import final_assessment

router = APIRouter(prefix='/api', tags=['Final HR recommendations'])


@router.get('/applications/{application_id}/final-assessment')
def get_final_assessment(application_id: UUID, db: Session = Depends(get_db), user: User = Depends(require(Permission.APPROVE_SHORTLIST))):
    return final_assessment(db, user, application_id)
