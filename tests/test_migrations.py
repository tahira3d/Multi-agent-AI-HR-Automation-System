"""Fresh-install and populated Phase 1 upgrade/downgrade checks."""
from pathlib import Path
from uuid import uuid4
from datetime import datetime, timezone
import json

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]


def test_migrations_preserve_legacy_users_requisitions_and_audit_chain(tmp_path, monkeypatch):
    url = 'sqlite:///' + (tmp_path / 'legacy.sqlite3').as_posix()
    monkeypatch.setenv('DATABASE_URL', url)
    config = Config(str(ROOT / 'backend' / 'alembic.ini'))
    command.upgrade(config, '0001_phase1')
    engine = create_engine(url)
    owner_id, req_id = uuid4().hex, uuid4().hex
    rubric = {'criteria': [{'key': 'required_skills', 'label': 'Required skills', 'weight': 100}]}
    with engine.begin() as connection:
        connection.execute(text('INSERT INTO users (user_id,name,email,password_hash,role,is_active,created_at) VALUES (:id,:name,:email,:hash,:role,1,:time)'),
            {'id': owner_id, 'name': 'Legacy Synthetic HR', 'email': 'legacy@synthetic.test', 'hash': 'legacy-hash-preserved', 'role': 'recruiter', 'time': datetime.now(timezone.utc)})
        connection.execute(text('INSERT INTO requisitions (requisition_id,created_by,job_title,job_description,required_skills,preferred_skills,screening_questions,rubric,status,created_at) VALUES (:id,:owner,:title,:description,:skills,:preferred,:questions,:rubric,:status,:time)'),
            {'id': req_id, 'owner': owner_id, 'title': 'Legacy backend role', 'description': 'Build Python APIs using PostgreSQL databases.', 'skills': '["Python"]', 'preferred': '[]', 'questions': '[]', 'rubric': json.dumps(rubric), 'status': 'open', 'time': datetime.now(timezone.utc)})
        connection.execute(text('INSERT INTO audit_events (seq,action,details,created_at,prev_hash,hash) VALUES (1,:action,:details,:time,:previous,:hash)'),
            {'action': 'legacy_event', 'details': '{}', 'time': '2026-01-01T00:00:00+00:00', 'previous': '0'*64, 'hash': 'a'*64})
    command.upgrade(config, 'head')
    with engine.begin() as connection:
        for name in ('checkpoints', 'checkpoint_blobs', 'checkpoint_writes', 'checkpoint_migrations'):
            connection.execute(text(f'CREATE TABLE {name} (marker TEXT)'))
    # Foreign-owned checkpoint tables must never appear as drop operations.
    command.check(config)
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT password_hash FROM users')) == 'legacy-hash-preserved'
        assert connection.scalar(text('SELECT token_version FROM users')) == 0
        assert connection.scalar(text('SELECT version FROM rubric_versions')) == 1
        assert connection.scalar(text('SELECT hash FROM audit_events')) == 'a'*64
        assert connection.scalar(text('SELECT event_id FROM audit_events'))
        assert inspect(connection).get_pk_constraint('audit_events')['constrained_columns'] == ['event_id']
    command.downgrade(config, '0001_phase1')
    command.upgrade(config, 'head')
    command.check(config)
    engine.dispose()
