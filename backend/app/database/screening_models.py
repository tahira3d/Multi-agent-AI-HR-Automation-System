"""Durable local screening state. Raw audio is never retained."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column
from app.database.connection import Base


def _now():
    return datetime.now(timezone.utc)


class ScreeningSession(Base):
    __tablename__ = 'screening_sessions'
    __table_args__ = (UniqueConstraint('screening_id', name='uq_screening_session_legacy'),
                     UniqueConstraint('application_id', name='uq_screening_session_application'))
    screening_session_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    screening_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('screenings.screening_id', ondelete='CASCADE'))
    application_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('applications.application_id', ondelete='CASCADE'), index=True)
    candidate_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('candidates.candidate_id', ondelete='CASCADE'), index=True)
    requisition_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('requisitions.requisition_id', ondelete='CASCADE'), index=True)
    evaluation_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('evaluations.evaluation_id', ondelete='RESTRICT'))
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey('users.user_id'))
    status: Mapped[str] = mapped_column(String(48), default='consent_requested', index=True)
    language: Mapped[str] = mapped_column(String(8), default='en')
    consent_status: Mapped[str] = mapped_column(String(32), default='requested')
    retain_transcript: Mapped[bool] = mapped_column(Boolean, default=False)
    context_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    model_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScreeningQuestion(Base):
    __tablename__ = 'screening_questions'
    __table_args__ = (UniqueConstraint('screening_session_id', 'question_order', name='uq_screening_question_order'),)
    question_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    screening_session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('screening_sessions.screening_session_id', ondelete='CASCADE'), index=True)
    parent_question_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey('screening_questions.question_id', ondelete='CASCADE'), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    topic: Mapped[str] = mapped_column(String(100), default='job_requirement')
    source_reference: Mapped[str] = mapped_column(String(240), default='hr_approved')
    question_order: Mapped[int] = mapped_column(Integer)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    is_follow_up: Mapped[bool] = mapped_column(Boolean, default=False)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unresolved_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ScreeningAnswer(Base):
    __tablename__ = 'screening_answers'
    __table_args__ = (UniqueConstraint('screening_session_id', 'audio_sha256', name='uq_screening_audio_digest'),
                     UniqueConstraint('question_id', name='uq_screening_answer_question'))
    answer_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    screening_session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('screening_sessions.screening_session_id', ondelete='CASCADE'), index=True)
    question_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('screening_questions.question_id', ondelete='CASCADE'), index=True)
    transcript_text: Mapped[str] = mapped_column(Text, default='')
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_status: Mapped[str] = mapped_column(String(32), default='processing')
    segments: Mapped[list] = mapped_column(JSON, default=list)
    audio_sha256: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(120), default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    audio_storage_reference: Mapped[str | None] = mapped_column(String(240), nullable=True)


class ScreeningReport(Base):
    __tablename__ = 'screening_reports'
    __table_args__ = (UniqueConstraint('screening_session_id', name='uq_screening_report_session'),)
    report_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    screening_session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('screening_sessions.screening_session_id', ondelete='CASCADE'), index=True)
    structured_summary: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ScreeningCommand(Base):
    __tablename__ = 'screening_commands'
    __table_args__ = (UniqueConstraint('screening_session_id', 'request_id', name='uq_screening_command_request'),)
    command_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    screening_session_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey('screening_sessions.screening_session_id', ondelete='CASCADE'), index=True)
    request_id: Mapped[str] = mapped_column(String(128))
    operation: Mapped[str] = mapped_column(String(40))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    result_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
