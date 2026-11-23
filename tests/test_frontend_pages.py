"""Isolated page smoke tests and mandatory human decision controls."""

from types import SimpleNamespace
from unittest.mock import patch
import pytest
from streamlit.testing.v1 import AppTest

JOB = {
    "requisition_id": "11111111-1111-1111-1111-111111111111",
    "job_title": "Synthetic backend developer",
    "job_description": "Develop tested Python APIs using PostgreSQL databases.",
    "required_skills": ["Python"],
    "preferred_skills": [],
    "min_experience_years": 1,
    "location": "Remote",
    "work_arrangement": "remote",
    "screening_questions": [],
    "status": "open",
    "rubric_version": 1,
    "rubric": {
        "criteria": [
            {
                "key": "required_skills",
                "label": "Required skills",
                "weight": 100,
                "justification": "Required for the backend role",
            }
        ]
    },
}
APPLICATION = {
    "application_id": "22222222-2222-2222-2222-222222222222",
    "candidate_name": "Synthetic Candidate",
    "candidate_id": "33333333-3333-3333-3333-333333333333",
    "current_status": "Scored \u2014 HR Review",
    "parse_status": "parsed",
}
EVALUATION = {
    "evaluation_id": "44444444-4444-4444-4444-444444444444",
    "application_id": APPLICATION["application_id"],
    "overall_score": 80,
    "criterion_scores": {"required_skills": 80},
    "criterion_findings": {"required_skills": "explicit"},
    "criterion_explanations": {"required_skills": "Documented API project."},
    "evidence": [
        {
            "criterion": "required_skills",
            "source_text": "Built Python APIs.",
            "explanation": "Direct project evidence.",
            "evidence_type": "explicit",
            "source_start": 0,
            "source_end": 18,
        }
    ],
    "missing_or_unclear_items": ["Project scale is unclear."],
    "parsing_warnings": ["Synthetic formatting warning"],
    "recommendation_for_human_review": "Review project scope.",
    "model_version": "test-only-model",
    "rubric_version": 1,
    "version": 1,
    "created_at": "2026-01-01T00:00:00+00:00",
}


def wrapper(page):
    return f"""
import streamlit as st
st.session_state['access_token'] = 'test-only-session'
st.session_state['current_user'] = {{'role': 'recruiter'}}
from frontend.pages.{page} import render
render()
"""


def transport(calls, application):
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/api/requisitions"):
            payload = [JOB]
        elif url.endswith("/applications"):
            payload = [application]
        elif url.endswith("/rankings"):
            payload = {"items": [{**EVALUATION, **application, "rubric_outdated": False}], "total": 1}
        elif url.endswith("/evaluation"):
            payload = EVALUATION
        elif url.endswith("/history"):
            payload = [
                {
                    "created_at": EVALUATION["created_at"],
                    "action": "evaluation.completed",
                    "actor_id": "synthetic-reviewer",
                    "details": {"rubric_version": 1},
                }
            ]
        elif url.endswith("/dashboard"):
            payload = {
                "total_requisitions": 1,
                "total_resumes": 1,
                "total_evaluated": 1,
                "awaiting_review": 1,
                "approved_for_screening": 0,
                "recent_activity": [],
            }
        elif url.endswith("/shortlist-decision"):
            assert kwargs["json"]["evaluation_id"] == EVALUATION["evaluation_id"]
            application["current_status"] = "Approved for Screening"
            payload = {"status": application["current_status"]}
        else:
            raise AssertionError("Unexpected API path " + url)
        return SimpleNamespace(ok=True, status_code=200, content=b"json", json=lambda: payload)

    return request


@pytest.mark.parametrize(
    "page", ["dashboard", "create_job", "upload_resumes", "candidate_rankings", "candidate_review"]
)
def test_each_connected_page_renders_without_exception(page):
    calls = []
    with patch("requests.request", side_effect=transport(calls, dict(APPLICATION))):
        app = AppTest.from_string(wrapper(page), default_timeout=20).run()
        assert not app.exception


def test_review_requires_acknowledgment_then_sends_current_evaluation():
    calls = []
    application = dict(APPLICATION)
    with patch("requests.request", side_effect=transport(calls, application)):
        app = AppTest.from_string(wrapper("candidate_review"), default_timeout=20).run()
        app.button[0].click().run()
        assert app.error
        assert not any(method == "POST" for method, _, _ in calls)
        app.checkbox[0].check()
        app.button[0].click().run()
        assert not app.exception
        assert any(method == "POST" and path.endswith("/shortlist-decision") for method, path, _ in calls)
        assert application["current_status"] == "Approved for Screening"
        assert not app.button  # No second decision form after progression.


def test_interview_report_page_renders_when_evaluation_is_not_yet_available():
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/api/requisitions"):
            payload, ok, status = [JOB], True, 200
        elif url.endswith("/applications"):
            payload, ok, status = (
                [{**APPLICATION, "current_status": "Screening Completed — HR Review"}],
                True,
                200,
            )
        elif url.endswith("/interview-evaluation"):
            payload, ok, status = {"detail": "No current interview evaluation exists."}, False, 404
        else:
            raise AssertionError("Unexpected API path " + url)
        return SimpleNamespace(ok=ok, status_code=status, content=b"json", json=lambda: payload)

    with patch("requests.request", side_effect=request):
        app = AppTest.from_string(wrapper("interview_report"), default_timeout=20).run()
        assert not app.exception
        assert any("Generate interview evaluation" in button.label for button in app.button)
