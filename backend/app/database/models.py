"""Relational model following the blueprint ERD (Applications are a separate entity)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    Index,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.core.status import Status
from app.database.connection import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120))
    full_name = synonym("name")
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("CURRENT_TIMESTAMP")
    )
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("uq_refresh_tokens_token_hash", "token_hash", unique=True),)
    token_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Requisition(Base):
    __tablename__ = "requisitions"
    requisition_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"))
    job_title: Mapped[str] = mapped_column(String(120))
    job_description: Mapped[str] = mapped_column(Text)
    required_skills: Mapped[list] = mapped_column(JSON, default=list)
    preferred_skills: Mapped[list] = mapped_column(JSON, default=list)
    min_experience_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    work_arrangement: Mapped[str | None] = mapped_column(String(120), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    screening_questions: Mapped[list] = mapped_column(JSON, default=list)
    rubric: Mapped[dict] = mapped_column(JSON)
    evaluation_rubric = synonym("rubric")
    minimum_experience = synonym("min_experience_years")
    rubric_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    applications: Mapped[list["Application"]] = relationship(back_populates="requisition")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("CURRENT_TIMESTAMP")
    )


class Candidate(Base):
    __tablename__ = "candidates"
    candidate_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    full_name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True, unique=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=text("CURRENT_TIMESTAMP")
    )


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("candidate_id", "requisition_id", name="uq_candidate_requisition"),)
    application_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.candidate_id"), index=True)
    requisition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requisitions.requisition_id"), index=True)
    current_status: Mapped[str] = mapped_column(String(60), default=Status.RESUME_RECEIVED.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    workflow_thread_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    processing_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluation_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    requisition: Mapped[Requisition] = relationship(back_populates="applications")
    candidate: Mapped[Candidate] = relationship()
    resume_files: Mapped[list["ResumeFile"]] = relationship(back_populates="application")


class ResumeFile(Base):
    __tablename__ = "resume_files"
    __table_args__ = (UniqueConstraint("application_id", "sha256", name="uq_resume_app_hash"),)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.application_id"), index=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(255))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    file_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    parse_status: Mapped[str] = mapped_column(String(30))
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # sensitive: never log
    uploaded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at = synonym("uploaded_at")
    resume_file_id = synonym("file_id")
    storage_reference = synonym("storage_key")
    content_hash = synonym("sha256")
    parsing_status = synonym("parse_status")
    parsing_warnings = synonym("warnings")

    application: Mapped[Application] = relationship(back_populates="resume_files")


class Evaluation(Base):  # filled by Agent 1 (Phase 2); each run is a new version
    __tablename__ = "evaluations"
    __table_args__ = (
        UniqueConstraint("application_id", "version", name="uq_evaluation_version"),
        Index(
            "uq_active_evaluation",
            "application_id",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active"),
        ),
    )
    evaluation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.application_id"), index=True)
    resume_file_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resume_files.file_id"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    rubric_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    rubric_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    request_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    total_score: Mapped[float] = mapped_column(Float)
    criterion_scores: Mapped[dict] = mapped_column(JSON)
    criterion_findings: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    criterion_explanations: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    evidence: Mapped[list] = mapped_column(JSON)
    missing_or_unclear: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    model_version: Mapped[str] = mapped_column(String(80))
    recommendation_for_human_review: Mapped[str] = mapped_column(Text, default="", server_default="")
    overall_score = synonym("total_score")
    missing_or_unclear_items = synonym("missing_or_unclear")
    parsing_warnings = synonym("warnings")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Screening(Base):  # Agent 2 (Phase 3)
    __tablename__ = "screenings"
    screening_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.application_id"), index=True)
    call_status: Mapped[str] = mapped_column(String(30))
    consent_status: Mapped[str] = mapped_column(String(30))
    answers: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Interview(Base):  # Agent 3 (Phase 4)
    __tablename__ = "interviews"
    interview_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.application_id"), index=True)
    interviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    start_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_utc: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)  # IANA id
    status: Mapped[str] = mapped_column(String(30), default="proposed")


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (UniqueConstraint("evaluation_id", "approval_type", name="uq_evaluation_approval"),)
    approval_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.application_id"), index=True)
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluations.evaluation_id"), nullable=True
    )
    reviewed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"))
    approval_type: Mapped[str] = mapped_column(String(30))  # shortlist | screening | invitation
    decision: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Communication(Base):
    __tablename__ = "communications"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_comm_idempotency"),)
    communication_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("applications.application_id"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    provider_reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditEvent(Base):
    """Append-only, hash-chained (see services/audit_chain.py). Never updated or deleted."""

    __tablename__ = "audit_events"
    seq: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    application_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String(40))  # ISO-8601 string, hashed verbatim
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)


class RubricVersion(Base):
    __tablename__ = "rubric_versions"
    __table_args__ = (UniqueConstraint("requisition_id", "version", name="uq_rubric_version"),)
    rubric_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    requisition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requisitions.requisition_id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    rubric: Mapped[dict] = mapped_column(JSON)
    job_snapshot: Mapped[dict] = mapped_column(JSON)
    approved_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditMutex(Base):
    __tablename__ = "audit_mutex"
    lock_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class InterviewEvaluation(Base):
    """Immutable Agent 3 result derived only from confirmed candidate quotes."""

    __tablename__ = "interview_evaluations"
    __table_args__ = (UniqueConstraint("screening_session_id", "version", name="uq_interview_eval_version"),)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.application_id", ondelete="CASCADE"), index=True
    )
    screening_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("screening_sessions.screening_session_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    rubric_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    criterion_results: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_coverage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    review_flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    comparison: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    followup_questions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(40), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), default="deterministic-v1", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), default="interview-v1", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class InterviewScoreOverride(Base):
    __tablename__ = "interview_score_overrides"
    __table_args__ = (UniqueConstraint("evaluation_id", "criterion", name="uq_interview_override_criterion"),)
    override_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_evaluations.evaluation_id", ondelete="CASCADE"), index=True
    )
    criterion: Mapped[str] = mapped_column(String(120), nullable=False)
    original_level: Mapped[int] = mapped_column(Integer, nullable=False)
    new_level: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class GeneratedReport(Base):
    __tablename__ = "generated_reports"
    __table_args__ = (
        UniqueConstraint("evaluation_id", "format", "version", name="uq_generated_report_version"),
    )
    report_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interview_evaluations.evaluation_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    storage_reference: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    generated_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FairnessRun(Base):
    __tablename__ = "fairness_runs"
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    stage: Mapped[str] = mapped_column(String(40), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class FairnessFinding(Base):
    __tablename__ = "fairness_findings"
    finding_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=_uuid)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fairness_runs.run_id", ondelete="CASCADE"), index=True
    )
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    cohort: Mapped[str] = mapped_column(String(160), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    acknowledgement_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# Register Phase 3 tables without changing the existing candidate/application identities.
from app.database import screening_models as screening_models  # noqa: E402,F401
