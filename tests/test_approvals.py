"""Business DB + actual durable checkpoints + deterministic mocked provider."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.status import Status
from app.database.connection import Base
from app.database.models import Application, Approval, Candidate, Evaluation, Requisition, ResumeFile, User
from app.services.evaluation_service import EvaluationFailure, EvaluationService
from app.integrations.azure_openai import ProviderUnavailableError

QUOTE = "Developed Python and FastAPI REST APIs using PostgreSQL."


class MockResult:
    def __init__(self, quote=QUOTE):
        self.quote = quote

    def model_dump(self, **kwargs):
        return {"overall_score": 99, "criterion_scores": {"required_skills": 80},
            "criterion_findings": {"required_skills": "explicit"},
            "criterion_explanations": {"required_skills": "Direct backend project evidence."},
            "evidence": [{"criterion": "required_skills", "source_text": self.quote,
                "explanation": "Python API project.", "evidence_type": "explicit",
                "source_start": 0, "source_end": len(self.quote)}],
            "missing_or_unclear_items": ["Duration is unclear."], "parsing_warnings": [],
            "recommendation_for_human_review": "Review project depth with HR.",
            "model_version": "mocked-provider-test-only"}


class MockAgent:
    def __init__(self, fail=False):
        self.calls, self.fail, self.quote = 0, fail, QUOTE

    def evaluate(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise ProviderUnavailableError("Synthetic outage")
        return MockResult(self.quote)


@pytest.fixture
def scenario(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    owner = User(user_id=uuid4(), name="Synthetic HR Owner", email="owner@synthetic.test", password_hash="test-only", role="recruiter")
    stranger = User(user_id=uuid4(), name="Other HR", email="other@synthetic.test", password_hash="test-only", role="recruiter")
    db.add_all([owner, stranger])
    db.flush()
    job = Requisition(created_by=owner.user_id, job_title="Backend Developer",
        job_description="Build Python APIs using PostgreSQL for business workflows.",
        required_skills=["Python"], preferred_skills=[], status="open", rubric_version=1,
        rubric={"criteria": [{"key": "required_skills", "label": "Required skills", "weight": 100, "justification": "Python backend role"}]})
    candidate = Candidate(full_name="Synthetic Candidate", email="candidate@synthetic.test")
    db.add_all([job, candidate])
    db.flush()
    application = Application(candidate_id=candidate.candidate_id, requisition_id=job.requisition_id, current_status=Status.RESUME_RECEIVED)
    db.add(application)
    db.flush()
    resume = ResumeFile(application_id=application.application_id, original_filename="synthetic.pdf",
        storage_key="synthetic.pdf", sha256="a" * 64, file_type="pdf", file_size=100,
        parse_status="parsed", warnings=[], extracted_text=QUOTE, uploaded_by=owner.user_id)
    db.add(resume)
    db.commit()
    settings = SimpleNamespace(database_url="sqlite://", checkpoint_database_url=None,
                               checkpoint_sqlite_path=tmp_path / "workflow.sqlite3")
    agent = MockAgent()
    s = SimpleNamespace(engine=engine, db=db, owner=owner, stranger=stranger, job=job, application=application,
        resume=resume, settings=settings, agent=agent)
    s.service = EvaluationService(db, owner, settings=settings, agent=agent)
    try:
        yield s
    finally:
        db.close()
        engine.dispose()


def test_scoring_rankings_and_human_approval_survive_service_restart(scenario):
    s = scenario
    result = s.service.evaluate(s.application.application_id)
    assert result["status"] == Status.SCORED_HR_REVIEW
    assert result["evaluation"]["overall_score"] == 80  # Python recomputes the deliberately wrong 99.
    assert s.db.scalar(select(func.count()).select_from(Approval)) == 0
    assert s.service.rankings(s.job.requisition_id)["items"][0]["candidate_name"] == "Synthetic Candidate"
    new_db = Session(s.engine, expire_on_commit=False)
    try:
        restarted = EvaluationService(new_db, new_db.get(User, s.owner.user_id), settings=s.settings, agent=MockAgent(fail=True))
        args = dict(evaluation_id=result["evaluation"]["evaluation_id"], decision="approve", reason="Reviewed evidence.")
        decision = restarted.decide(s.application.application_id, **args)
        assert decision["status"] == Status.APPROVED_FOR_SCREENING
        assert decision["reviewed_by"] == str(s.owner.user_id)
        assert restarted.decide(s.application.application_id, **args)["approval_id"] == decision["approval_id"]
        assert new_db.scalar(select(func.count()).select_from(Approval)) == 1
    finally:
        new_db.close()


def test_repeat_evaluation_and_revised_rubric_retain_history(scenario):
    s = scenario
    first = s.service.evaluate(s.application.application_id)
    repeated = s.service.evaluate(s.application.application_id)
    assert repeated["evaluation"]["evaluation_id"] == first["evaluation"]["evaluation_id"]
    assert s.agent.calls == 1
    s.job.rubric_version = 2
    s.db.commit()
    second = s.service.evaluate(s.application.application_id)
    assert second["evaluation"]["version"] == 2 and s.agent.calls == 2
    values = s.db.scalars(select(Evaluation).order_by(Evaluation.version)).all()
    assert [v.is_active for v in values] == [False, True]
    assert s.service.latest_evaluation(s.application.application_id, 1).evaluation_id == values[0].evaluation_id


def test_other_recruiter_cannot_read_rank_or_approve(scenario):
    s = scenario
    result = s.service.evaluate(s.application.application_id)
    denied = EvaluationService(s.db, s.stranger, settings=s.settings)
    for action in [lambda: denied.latest_evaluation(s.application.application_id),
                   lambda: denied.rankings(s.job.requisition_id),
                   lambda: denied.decide(s.application.application_id, evaluation_id=result["evaluation"]["evaluation_id"], decision="approve")]:
        with pytest.raises(HTTPException) as error:
            action()
        assert error.value.status_code == 404


def test_stale_rubric_and_conflicting_decisions_rejected(scenario):
    s = scenario
    result = s.service.evaluate(s.application.application_id)
    eval_id = result["evaluation"]["evaluation_id"]
    s.job.rubric_version = 2
    s.db.commit()
    with pytest.raises(HTTPException) as stale:
        s.service.decide(s.application.application_id, evaluation_id=eval_id, decision="approve")
    assert stale.value.status_code == 409
    s.job.rubric_version = 1
    s.db.commit()
    s.service.decide(s.application.application_id, evaluation_id=eval_id, decision="approve")
    with pytest.raises(HTTPException) as conflict:
        s.service.decide(s.application.application_id, evaluation_id=eval_id, decision="decline", reason="Changed mind")
    assert conflict.value.status_code == 409


@pytest.mark.parametrize("decision,status", [("decline", Status.CLOSED), ("hold", Status.NEEDS_MANUAL_REVIEW)])
def test_decline_and_hold_require_human_reasons(scenario, decision, status):
    s = scenario
    result = s.service.evaluate(s.application.application_id)
    with pytest.raises(HTTPException) as missing:
        s.service.decide(s.application.application_id, evaluation_id=result["evaluation"]["evaluation_id"], decision=decision)
    assert missing.value.status_code == 422
    result = s.service.decide(s.application.application_id, evaluation_id=result["evaluation"]["evaluation_id"],
                              decision=decision, reason="Human evidence review")
    assert result["status"] == status


def test_provider_failure_recoverable_without_fake_score(scenario):
    s = scenario
    s.agent.fail = True
    with pytest.raises(EvaluationFailure):
        s.service.evaluate(s.application.application_id)
    assert s.db.get(Application, s.application.application_id).current_status == Status.NEEDS_MANUAL_REVIEW
    assert s.db.scalar(select(func.count()).select_from(Evaluation)) == 0
    s.agent.fail = False
    result = s.service.evaluate(s.application.application_id)
    assert result["evaluation"]["version"] == 1


def test_untraceable_evidence_never_persisted(scenario):
    s = scenario
    s.agent.quote = "Invented experience absent from this resume."
    with pytest.raises(EvaluationFailure):
        s.service.evaluate(s.application.application_id)
    assert s.db.scalar(select(func.count()).select_from(Evaluation)) == 0


def test_concurrent_processing_claim_prevents_duplicate_work(scenario):
    s = scenario
    app_id = s.application.application_id
    s.service._claim(s.application, 1, f"hiring:{app_id}:v1")
    other = Session(s.engine)
    try:
        service = EvaluationService(other, other.get(User, s.owner.user_id), settings=s.settings)
        with pytest.raises(HTTPException) as busy:
            service._claim(other.get(Application, app_id), 1, f"hiring:{app_id}:v1")
        assert busy.value.status_code == 409
    finally:
        other.close()
        s.service._release(app_id)


def test_hold_retry_stays_held_and_force_keeps_decision_history(scenario):
    s = scenario
    first = s.service.evaluate(s.application.application_id)
    s.service.decide(s.application.application_id, evaluation_id=first['evaluation']['evaluation_id'],
                     decision='hold', reason='HR needs more project context')
    repeated = s.service.evaluate(s.application.application_id)
    assert repeated['status'] == Status.NEEDS_MANUAL_REVIEW
    assert s.agent.calls == 1
    second = s.service.evaluate(s.application.application_id, force_reevaluation=True)
    assert second['evaluation']['version'] == 2
    s.service.decide(s.application.application_id, evaluation_id=second['evaluation']['evaluation_id'], decision='approve')
    assert len(s.db.scalars(select(Approval)).all()) == 2


def test_stale_processing_claim_cannot_reopen_human_approved_application(scenario):
    s = scenario
    app_id = s.application.application_id
    s.db.execute(update(Application).where(Application.application_id == app_id)
                 .values(current_status=Status.APPROVED_FOR_SCREENING), execution_options={'synchronize_session': False})
    s.db.commit()
    with pytest.raises(HTTPException) as conflict:
        s.service._claim(s.application, 1, f'hiring:{app_id}:v1')
    assert conflict.value.status_code == 409


def test_committed_evaluation_recovers_lost_checkpoint_without_provider_call(scenario):
    from app.workflows.hiring_graph import delete_workflow_checkpoints
    s = scenario
    first = s.service.evaluate(s.application.application_id)
    delete_workflow_checkpoints(s.settings, str(s.application.application_id), 1)
    s.agent.fail = True
    result = s.service.decide(s.application.application_id,
        evaluation_id=first['evaluation']['evaluation_id'], decision='approve')
    assert result['status'] == Status.APPROVED_FOR_SCREENING
    assert s.agent.calls == 1
