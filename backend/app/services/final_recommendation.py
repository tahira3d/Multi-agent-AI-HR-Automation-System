"""Agent 3 evidence-grounded recommendation assembly.

This module deliberately produces decision support only. It never changes an
application status, creates an approval, or sends a communication.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import accessible_application
from app.database.models import Evaluation
from app.database.screening_models import ScreeningReport, ScreeningSession


def _valid_score(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100


def final_assessment(db: Session, user, application_id: UUID) -> dict:
    app = accessible_application(db, application_id, user)
    evaluation = db.scalar(select(Evaluation).where(
        Evaluation.application_id == app.application_id, Evaluation.is_active.is_(True)))
    session = db.scalar(select(ScreeningSession).where(
        ScreeningSession.application_id == app.application_id).order_by(ScreeningSession.created_at.desc()))
    report = db.scalar(select(ScreeningReport).where(
        ScreeningReport.screening_session_id == session.screening_session_id)) if session else None

    missing, limitations = [], []
    if evaluation is None:
        missing.append('Active Agent 1 resume evaluation is unavailable.')
    if report is None:
        missing.append('Completed Agent 2 interview evaluation is unavailable.')
    interview = (report.structured_summary or {}) if report else {}
    resume_score = evaluation.total_score if evaluation else None
    interview_score = interview.get('interview_score')
    interview_score_ok = _valid_score(interview_score) and interview.get('score_status') == 'complete'
    if report and not interview_score_ok:
        limitations.append('Agent 2 did not provide a complete valid interview score; no final score was calculated.')
    overall = round(resume_score * .4 + float(interview_score) * .6, 1) if _valid_score(resume_score) and interview_score_ok else None
    if overall is None:
        recommendation = 'additional_review_required'
        reason = 'A complete recommendation requires validated resume and interview scores.'
    elif overall >= 70:
        recommendation = 'recommended_for_hr_shortlist_review'
        reason = 'The available job-related scores meet the advisory review threshold; HR must review supporting evidence.'
    else:
        recommendation = 'additional_review_required'
        reason = 'The available evidence needs HR review before any decision.'
    identity_ok = bool(evaluation and session and evaluation.application_id == session.application_id == app.application_id
                       and session.candidate_id == app.candidate_id)
    if report and str(interview.get('candidate_id')) != str(app.candidate_id):
        identity_ok = False
        missing.append('Agent 2 report candidate identifier does not match this application.')
    if not identity_ok and not missing:
        missing.append('Agent reports do not belong to the same application and candidate.')
    confidence = 'high' if overall is not None and not limitations else ('medium' if evaluation else 'low')
    return {
        'agent': 'Agent 3', 'agent_role': 'final_hiring_recommendation_and_hr_workflow',
        'candidate_id': str(app.candidate_id), 'candidate_name': app.candidate.full_name,
        'candidate_email': app.candidate.email, 'job_id': str(app.requisition_id), 'job_title': app.requisition.job_title,
        'input_validation': {'agent_1_report_received': evaluation is not None, 'agent_2_report_received': report is not None,
            'candidate_identity_verified': identity_ok, 'data_quality_status': 'complete' if overall is not None and identity_ok else 'incomplete', 'missing_information': missing},
        'scores': {'resume_score': resume_score, 'interview_score': interview_score if interview_score_ok else None,
            'overall_score': overall, 'scoring_weights': {'resume': 40, 'interview': 60}, 'score_status': 'complete' if overall is not None else 'provisional'},
        'skills_matching': list((evaluation.criterion_scores or {}).keys()) if evaluation else [],
        'experience_assessment': interview.get('relevant_experience_statements', []),
        'candidate_strengths': interview.get('candidate_strengths', []), 'candidate_weaknesses': interview.get('candidate_weaknesses', []),
        'cross_agent_analysis': {'supported_claims': [], 'unverified_claims': interview.get('resume_claims_needing_clarification', []),
            'contradictions': [], 'additional_evidence': interview.get('evidence', [])},
        'evidence_assessment': {'supporting_evidence': (evaluation.evidence if evaluation else []) + interview.get('evidence', []),
            'missing_evidence': missing, 'confidence_level': confidence, 'assessment_limitations': limitations},
        'fairness_assessment': {'status': 'not_assessed', 'potential_risks': [],
            'limitations': ['Protected characteristics are not collected or inferred; group fairness analysis cannot be completed.']},
        'candidate_ranking': None, 'ai_recommendation': {'status': recommendation, 'reason': reason, 'recommended_hr_action': 'review_evidence'},
        'hr_review': {'status': 'pending', 'reviewer_id': None, 'decision': None, 'comments': None, 'approval_timestamp': None},
        'candidate_communication': {'email_eligible': False, 'email_type': None, 'email_status': 'not_sent', 'email_address': app.candidate.email, 'sent_timestamp': None, 'delivery_reference': None},
        'audit_record': {'agent_1_reference': str(evaluation.evaluation_id) if evaluation else None,
            'agent_2_reference': str(report.report_id) if report else None, 'evaluation_timestamp': datetime.now(timezone.utc).isoformat(), 'evaluation_version': '4.0'},
    }
