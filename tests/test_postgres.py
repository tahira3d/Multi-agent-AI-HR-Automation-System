"""Opt-in real PostgreSQL migrations + persistent review checkpoints."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.status import Status
from app.database.models import Application, Candidate, Requisition, ResumeFile, User
from app.services.evaluation_service import EvaluationService
from tests.test_approvals import MockAgent, QUOTE


@pytest.mark.live
def test_real_postgres_migrations_and_restart_handoff(monkeypatch):
    url = os.getenv("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set TEST_POSTGRES_URL to opt in to a real PostgreSQL verification")
    schema = "hr_test_" + uuid4().hex
    control = create_engine(url, isolation_level="AUTOCOMMIT")
    engine = None
    with control.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
        monkeypatch.setenv("DATABASE_URL", scoped_url)
        config = Config(str(Path(__file__).resolve().parents[1] / "backend" / "alembic.ini"))
        command.upgrade(config, "head")
        command.check(config)
        engine = create_engine(scoped_url)
        settings = SimpleNamespace(database_url=scoped_url, checkpoint_database_url=scoped_url)
        with Session(engine, expire_on_commit=False) as db:
            user = User(name="Synthetic PostgreSQL HR", email="pg@synthetic.test", role="recruiter", password_hash="test-only")
            db.add(user)
            db.flush()
            job = Requisition(created_by=user.user_id, job_title="Python developer",
                job_description="Build tested Python APIs using PostgreSQL databases.",
                rubric={"criteria": [{"key": "required_skills", "label": "Required skills", "weight": 100}]})
            candidate = Candidate(full_name="Synthetic Candidate")
            db.add_all([job, candidate])
            db.flush()
            application = Application(candidate_id=candidate.candidate_id, requisition_id=job.requisition_id)
            db.add(application)
            db.flush()
            db.add(ResumeFile(application_id=application.application_id, original_filename="synthetic.pdf",
                storage_key="synthetic.pdf", sha256="b"*64, file_type="pdf", parse_status="parsed",
                warnings=[], extracted_text=QUOTE, uploaded_by=user.user_id))
            db.commit()
            app_id, user_id = application.application_id, user.user_id
            result = EvaluationService(db, user, agent=MockAgent(), settings=settings).evaluate(app_id)
            evaluation_id = result["evaluation"]["evaluation_id"]
            assert result["status"] == Status.SCORED_HR_REVIEW
        with Session(engine) as db:
            restarted = EvaluationService(db, db.get(User, user_id), agent=MockAgent(fail=True), settings=settings)
            assert restarted.decide(app_id, evaluation_id=evaluation_id, decision="approve")["status"] == Status.APPROVED_FOR_SCREENING
        command.check(config)  # LangGraph's own tables are outside application migrations.
    finally:
        if engine:
            engine.dispose()
        with control.connect() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()
