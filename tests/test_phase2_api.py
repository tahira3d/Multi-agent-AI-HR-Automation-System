"""Connected API acceptance path, ownership, token revocation, and guarded re-evaluation."""
from pathlib import Path
import json
import re

import pytest

from sqlalchemy import select
from app.agents.resume_agent import ResumeScoringAgent
from app.database.models import Application, Approval, Communication, Evaluation, Interview, RubricVersion, Screening, User
from app.schemas.evaluation import LLMEvaluation

from tests.test_api_phase1 import client as client, login, make_req

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data" / "synthetic_resumes"


class TestEvidenceProvider:
    __test__ = False

    def generate(self, messages, *, validator=None):
        payload = json.loads(messages[1]["content"])
        source = next(line for line in payload["resume_text"].splitlines() if "FastAPI" in line)
        label = re.match(r"\[(L[0-9]+)\]", source)
        source = "[[" + label.group(1) + "]]"
        criteria = []
        for criterion in payload["rubric"]["criteria"]:
            matched = criterion["key"] == "required_skills"
            criteria.append({"key": criterion["key"], "score": 80 if matched else 0,
                "finding": "explicit" if matched else "not_found",
                "explanation": "Backend delivery evidence." if matched else "Not established in this synthetic test.",
                "evidence": [{"source_text": source, "explanation": "Backend delivery.", "evidence_type": "explicit"}] if matched else []})
        value = LLMEvaluation(criteria=criteria, missing_or_unclear_items=["Review remaining criteria with HR."],
                              recommendation_for_human_review="Human review is required.")
        if validator:
            validator(value)
        return value, "mocked-api-test-only"


def uploaded(client, headers, req_id):
    path = SAMPLES / "01_ayesha_raza.pdf"
    response = client.post(f"/api/requisitions/{req_id}/resumes", headers=headers,
                           files=[("files", (path.name, path.read_bytes(), "application/pdf"))])
    assert response.status_code == 201, response.text
    return response.json()["results"][0]["application_id"]


def test_job_upload_evaluate_rank_human_approval(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CHECKPOINT_SQLITE_PATH", str(tmp_path / "durable.sqlite3"))
    agent = ResumeScoringAgent(TestEvidenceProvider())
    monkeypatch.setattr("app.agents.resume_agent.get_resume_agent", lambda: agent)
    headers = login(client, "rec@t.test")
    req_id = make_req(client, headers)
    app_id = uploaded(client, headers, req_id)
    response = client.post(f"/api/applications/{app_id}/evaluate", headers=headers)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "Scored \u2014 HR Review"
    assert result["evaluation"]["overall_score"] == 32
    with client.TestSession() as db:
        assert db.scalar(select(Approval)) is None
        assert db.get(Application, __import__("uuid").UUID(app_id)).current_status == "Scored \u2014 HR Review"
    ranking = client.get(f"/api/requisitions/{req_id}/rankings", headers=headers)
    assert ranking.status_code == 200
    assert ranking.json()["items"][0]["application_id"] == app_id
    evaluation = client.get(f"/api/applications/{app_id}/evaluation", headers=headers).json()
    decision = {"evaluation_id": evaluation["evaluation_id"], "decision": "approve", "reason": "HR reviewed synthetic evidence."}
    response = client.post(f"/api/applications/{app_id}/shortlist-decision", headers=headers, json=decision)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "Approved for Screening"
    assert client.post(f"/api/applications/{app_id}/shortlist-decision", headers=headers, json=decision).json()["approval_id"] == response.json()["approval_id"]
    assert client.post(f"/api/applications/{app_id}/evaluate", headers=headers, json={"force_reevaluation": True}).status_code == 409
    with client.TestSession() as db:
        assert len(db.scalars(select(Evaluation)).all()) == 1
        assert len(db.scalars(select(Approval)).all()) == 1
        assert db.scalar(select(Communication)) is None
        assert db.scalar(select(Screening)) is None
        assert db.scalar(select(Interview)) is None
    history = client.get(f"/api/applications/{app_id}/history", headers=headers).json()
    assert {e["action"] for e in history} >= {"resume_uploaded", "evaluation.completed", "shortlist.human_decision"}
    human_event = next(event for event in history if event['action'] == 'shortlist.human_decision')
    assert human_event['reason'] == decision['reason']
    assert human_event['reviewed_by'] and human_event['reviewed_at']
    assert client.get("/api/audit/verify", headers=headers).json()["intact"]


def test_owner_scope_and_unauthorized_approval(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CHECKPOINT_SQLITE_PATH", str(tmp_path / "cp.sqlite3"))
    headers = login(client, "rec@t.test")
    req_id = make_req(client, headers)
    app_id = uploaded(client, headers, req_id)
    with client.TestSession() as db:
        from app.core.security import hash_password
        db.add(User(name="Other Recruiter", email="other@t.test", password_hash=hash_password("Passw0rd!Passw0rd"), role="recruiter"))
        db.commit()
    other = login(client, "other@t.test")
    assert client.get("/api/requisitions", headers=other).json() == []
    for path in [f"/api/requisitions/{req_id}", f"/api/requisitions/{req_id}/applications",
                 f"/api/requisitions/{req_id}/rankings", f"/api/applications/{app_id}/history",
                 f"/api/applications/{app_id}/evaluation"]:
        assert client.get(path, headers=other).status_code == 404
    assert client.post(f"/api/applications/{app_id}/evaluate", headers=other).status_code == 404
    interviewer = login(client, "int@t.test")
    assert client.post(f"/api/applications/{app_id}/shortlist-decision", headers=interviewer,
        json={"evaluation_id": "00000000-0000-0000-0000-000000000000", "decision": "approve"}).status_code == 403


def test_requisition_versioning_and_stale_decision(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CHECKPOINT_SQLITE_PATH", str(tmp_path / "cp.sqlite3"))
    monkeypatch.setattr("app.agents.resume_agent.get_resume_agent", lambda: ResumeScoringAgent(TestEvidenceProvider()))
    headers = login(client, "rec@t.test")
    req_id = make_req(client, headers)
    app_id = uploaded(client, headers, req_id)
    first = client.post(f"/api/applications/{app_id}/evaluate", headers=headers).json()["evaluation"]
    updated = client.patch(f"/api/requisitions/{req_id}", headers=headers,
                           json={"required_skills": ["Python", "FastAPI"], "expected_rubric_version": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["rubric_version"] == 2
    assert client.patch(f"/api/requisitions/{req_id}", headers=headers,
                        json={"required_skills": ["Python"], "expected_rubric_version": 1}).status_code == 409
    assert client.post(f"/api/applications/{app_id}/shortlist-decision", headers=headers,
        json={"evaluation_id": first["evaluation_id"], "decision": "approve"}).status_code == 409
    second = client.post(f"/api/applications/{app_id}/evaluate", headers=headers).json()["evaluation"]
    assert second["version"] == 2
    assert client.get(f"/api/applications/{app_id}/evaluation?version=1", headers=headers).json()["evaluation_id"] == first["evaluation_id"]
    with client.TestSession() as db:
        assert len(db.scalars(select(RubricVersion)).all()) == 2


def test_logout_revokes_tokens_and_validation_hides_password(client):
    headers = login(client, "rec@t.test")
    assert client.post("/api/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    private = "secret-value-not-for-errors"
    response = client.post("/api/auth/login", json={"email": "invalid", "password": private})
    assert response.status_code == 422
    assert private not in response.text


def test_protected_criteria_rejected(client):
    headers = login(client, "rec@t.test")
    response = client.post("/api/requisitions", headers=headers, json={"job_title": "Backend Developer",
        "job_description": "Build reliable Python APIs using PostgreSQL databases.",
        "rubric": {"criteria": [{"key": "gender", "label": "Gender", "weight": 100}]}})
    assert response.status_code == 422


def test_unconfigured_azure_cannot_create_score(client, monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "")
    monkeypatch.setenv("CHECKPOINT_SQLITE_PATH", str(tmp_path / "cp.sqlite3"))
    monkeypatch.setattr("app.agents.resume_agent.get_resume_agent", lambda: ResumeScoringAgent())
    headers = login(client, "rec@t.test")
    app_id = uploaded(client, headers, make_req(client, headers))
    response = client.post(f"/api/applications/{app_id}/evaluate", headers=headers)
    assert response.status_code == 503
    assert 'AZURE_OPENAI_ENDPOINT' in response.json()['detail']
    assert 'restart' in response.json()['detail']
    with client.TestSession() as db:
        assert db.scalar(select(Evaluation)) is None
        assert db.scalar(select(Approval)) is None


@pytest.mark.parametrize('kind,expected', [('authentication', 'GROQ_API_KEY'), ('rate_limit', 'rate limit')])
def test_groq_provider_errors_are_safe_and_preserve_frontend_status(client, monkeypatch, tmp_path, kind, expected):
    from app.integrations.groq_cloud import ProviderAuthenticationError, ProviderRateLimitError

    class FailingAgent:
        def evaluate(self, **kwargs):
            error = ProviderAuthenticationError if kind == 'authentication' else ProviderRateLimitError
            raise error('synthetic-private-provider-output')

    monkeypatch.setenv('CHECKPOINT_SQLITE_PATH', str(tmp_path / 'cp.sqlite3'))
    monkeypatch.setattr('app.agents.resume_agent.get_resume_agent', lambda: FailingAgent())
    headers = login(client, 'rec@t.test')
    app_id = uploaded(client, headers, make_req(client, headers))
    failed = client.post(f'/api/applications/{app_id}/evaluate', headers=headers)
    assert failed.status_code == 503
    assert expected in failed.json()['detail']
    assert 'synthetic-private' not in failed.text
    with client.TestSession() as db:
        assert db.scalar(select(Evaluation)) is None
        assert db.scalar(select(Approval)) is None
    history = client.get(f'/api/applications/{app_id}/history', headers=headers)
    assert history.status_code == 200 and 'synthetic-private' not in history.text


def test_health_exposes_groq_configuration_without_credentials(client, monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'synthetic-private-key')
    monkeypatch.setenv('GROQ_BASE_URL', 'https://api.groq.com/openai/v1')
    monkeypatch.setenv('GROQ_MODEL', 'openai/gpt-oss-120b')
    configured = client.get('/health')
    assert configured.status_code == 200
    assert configured.json() == {'status': 'ok', 'version': '0.2.0', 'provider': 'groq',
                                 'groq_configured': True, 'azure_configured': False}
    assert 'synthetic-private' not in configured.text
    monkeypatch.setenv('GROQ_API_KEY', '')
    assert client.get('/health').json()['groq_configured'] is False


def test_evidence_validation_error_does_not_blame_credentials_or_echo_private_values(client, monkeypatch, tmp_path):
    from app.integrations.azure_openai import InvalidEvaluationError
    from app.database.models import AuditEvent

    class FailingAgent:
        def evaluate(self, **kwargs):
            raise InvalidEvaluationError('synthetic-private-output-must-not-escape')

    monkeypatch.setenv('CHECKPOINT_SQLITE_PATH', str(tmp_path / 'cp.sqlite3'))
    monkeypatch.setattr('app.agents.resume_agent.get_resume_agent', lambda: FailingAgent())
    headers = login(client, 'rec@t.test')
    app_id = uploaded(client, headers, make_req(client, headers))
    failed = client.post(f'/api/applications/{app_id}/evaluate', headers=headers)
    assert failed.status_code == 422
    assert 'evidence-validation' in failed.json()['detail']
    assert 'key' not in failed.json()['detail']
    assert 'synthetic-private-output' not in failed.text
    with client.TestSession() as db:
        events = db.scalars(select(AuditEvent)).all()
        assert all('synthetic-private-output' not in json.dumps(event.details) for event in events)
    monkeypatch.setattr('app.agents.resume_agent.get_resume_agent', lambda: ResumeScoringAgent(TestEvidenceProvider()))
    recovered = client.post(f'/api/applications/{app_id}/evaluate', headers=headers)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()['evaluation']['version'] == 1
