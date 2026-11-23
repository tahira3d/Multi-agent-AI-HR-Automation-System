"""Safe, deterministic Phase 4 scoring from confirmed Agent 2 statements.

This intentionally does not infer traits or score delivery.  It is a fallback
for the existing local screening prototype; a configured structured LLM may be
added as an extraction adapter later, but reports never invent evidence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.status import Status, assert_transition
from app.database.models import Application, InterviewEvaluation, User
from app.database.screening_models import ScreeningReport, ScreeningSession
from app.services.audit_service import record_event

DEFAULT_RUBRIC = [
    ("technical_role_knowledge", "Technical and role knowledge", 35),
    ("relevant_experience_depth", "Relevant experience depth", 25),
    ("problem_solving_approach", "Problem-solving approach", 20),
    ("role_requirement_alignment", "Role requirement alignment", 10),
    ("answer_completeness", "Answer completeness", 10),
]
LEVEL_SCORE = {0: 0.0, 1: 25.0, 2: 50.0, 3: 75.0, 4: 100.0}


def _rubric() -> dict[str, Any]:
    return {
        "version": 1,
        "criteria": [
            {
                "key": key,
                "label": label,
                "weight": weight,
                "anchors": {
                    "0": "no verified statement",
                    "2": "a verified candidate statement",
                    "4": "specific verified evidence with outcome",
                },
            }
            for key, label, weight in DEFAULT_RUBRIC
        ],
    }


def _candidate_evidence(report: ScreeningReport) -> list[dict[str, str]]:
    rows = report.structured_summary.get("questions_and_answers", [])
    return [
        {
            "turn_id": str(row.get("transcript_reference", "")),
            "quote": str(row["candidate_statement"]),
            "topic": str(row.get("topic", "job_requirement")),
        }
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("candidate_statement"), str)
        and row["candidate_statement"].strip()
    ]


def evaluate(db: Session, application: Application, user: User) -> InterviewEvaluation:
    """Create an idempotent, evidence-cited evaluation for a completed screening."""
    current = db.scalar(
        select(InterviewEvaluation).where(
            InterviewEvaluation.application_id == application.application_id,
            InterviewEvaluation.is_current.is_(True),
        )
    )
    if current:
        return current
    session = db.scalar(
        select(ScreeningSession).where(ScreeningSession.application_id == application.application_id)
    )
    if session is None or session.status != "completed" or session.consent_status != "accepted":
        raise HTTPException(409, "A completed screening with accepted transcript consent is required.")
    report = db.scalar(
        select(ScreeningReport).where(ScreeningReport.screening_session_id == session.screening_session_id)
    )
    if report is None:
        raise HTTPException(409, "The completed screening report is not available.")
    evidence = _candidate_evidence(report)
    if not evidence:
        raise HTTPException(409, "Not scoreable: the screening has no confirmed candidate statements.")
    results, scored_weight = [], 0
    for key, label, weight in DEFAULT_RUBRIC:
        relevant = [
            item for item in evidence if item["topic"] in {key, "job_requirement", "hr_clarification"}
        ]
        if not relevant:
            results.append(
                {
                    "criterion": key,
                    "label": label,
                    "weight": weight,
                    "status": "insufficient_evidence",
                    "anchor_level": None,
                    "score": None,
                    "run_spread": 0,
                    "evidence": [],
                    "rationale": "No verified candidate statement addressed this criterion.",
                }
            )
            continue
        # A level is only an ordinal representation of verified statement depth;
        # it is not a judgement of voice, fluency, confidence, or personality.
        level = 3 if any(len(item["quote"]) >= 120 for item in relevant) else 2
        scored_weight += weight
        results.append(
            {
                "criterion": key,
                "label": label,
                "weight": weight,
                "status": "scored",
                "anchor_level": level,
                "score": LEVEL_SCORE[level],
                "run_spread": 0,
                "evidence": relevant,
                "rationale": "Based only on the cited candidate statement(s).",
            }
        )
    coverage = scored_weight / 100
    overall = (
        round(
            sum(item["score"] * item["weight"] for item in results if item["score"] is not None)
            / scored_weight,
            1,
        )
        if coverage >= 0.6
        else None
    )
    flags = [] if overall is not None else ["needs_more_information"]
    evaluation = InterviewEvaluation(
        application_id=application.application_id,
        screening_session_id=session.screening_session_id,
        rubric_snapshot=_rubric(),
        criterion_results=results,
        overall_score=overall,
        evidence_coverage=coverage,
        confidence=round(coverage, 2),
        review_flags=flags,
        comparison=[],
        followup_questions=[
            f"Please ask for a job-related example about {r['label']}."
            for r in results
            if r["status"] != "scored"
        ],
        recommendation="proceed_to_final_review" if overall is not None else "needs_more_information",
    )
    db.add(evaluation)
    if application.current_status != Status.INTERVIEW_SCORED_HR_REVIEW:
        assert_transition(application.current_status, Status.INTERVIEW_SCORED_HR_REVIEW)
        application.current_status = Status.INTERVIEW_SCORED_HR_REVIEW
    record_event(
        db,
        action="interview.evaluated",
        actor_id=user.user_id,
        application_id=application.application_id,
        entity_type="interview_evaluation",
        entity_id=str(evaluation.evaluation_id),
        details={"evidence_coverage": coverage, "scored": overall is not None},
    )
    db.commit()
    db.refresh(evaluation)
    return evaluation


def as_dict(value: InterviewEvaluation) -> dict[str, Any]:
    return {
        "evaluation_id": str(value.evaluation_id),
        "application_id": str(value.application_id),
        "interview_rubric_version": value.rubric_snapshot.get("version", 1),
        "overall_interview_score": value.overall_score,
        "evidence_coverage": value.evidence_coverage,
        "criteria": value.criterion_results,
        "review_flags": value.review_flags,
        "confidence": value.confidence,
        "resume_call_comparison": value.comparison,
        "followup_questions_for_human": value.followup_questions,
        "recommendation_for_human_review": value.recommendation,
        "model_version": value.model_version,
        "prompt_version": value.prompt_version,
        "created_at": value.created_at.isoformat()
        if value.created_at
        else datetime.now(timezone.utc).isoformat(),
    }
