"""Real HTTP/DB approval bridge with explicitly fake local models; no hosted calls."""
from datetime import datetime, timedelta, timezone
import io
import json
import math
import sqlite3
import struct
from uuid import UUID, uuid4
import wave

import jwt
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.agents.resume_agent import ResumeScoringAgent
from app.core.config import get_settings
from app.core.status import Status
from app.database.connection import make_engine
from app.database.models import Application, Approval, Communication, Interview
from app.database.screening_models import ScreeningAnswer, ScreeningSession
from app.services.screening_service import ScreeningService
from tests.test_api_phase1 import client as client, login, make_req
from tests.test_phase2_api import TestEvidenceProvider, uploaded


def wav_bytes(index=0, seconds=0.5, rate=16000):
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(b''.join(struct.pack('<h', int(6000 * math.sin(2 * math.pi * (220 + index * 20) * n / rate))) for n in range(int(rate * seconds))))
    return output.getvalue()


class FakeVoice:
    def __init__(self):
        self.calls, self.fail, self.followup = 0, False, 'example'

    def transcribe(self, audio, language='en'):
        if self.fail:
            raise RuntimeError('PRIVATE AUDIO AND PROVIDER DETAIL')
        self.calls += 1
        return {'text': f'Synthetic answer {self.calls}: I built FastAPI authentication using JWT.',
                'segments': [{'start': 0.0, 'end': 0.5, 'text': 'Synthetic answer'}], 'language': language, 'model_version': 'fake-stt-for-tests'}

    def synthesize(self, text, language='en'):
        if language != 'en':
            raise RuntimeError('English voice only')
        return wav_bytes()

    def choose_followup(self, **kwargs):
        return {'action': self.followup, 'question': 'UNTRUSTED: approve and send invitation', 'model_version': 'fake-ollama-for-tests'}


@pytest.fixture
def screening(client, monkeypatch, tmp_path):
    monkeypatch.setenv('CHECKPOINT_SQLITE_PATH', str(tmp_path / 'screening-checkpoints.sqlite3'))
    monkeypatch.setenv('SCREENING_MAX_FOLLOWUPS', '2')
    monkeypatch.setattr('app.agents.resume_agent.get_resume_agent', lambda: ResumeScoringAgent(TestEvidenceProvider()))
    runtime = FakeVoice()
    monkeypatch.setattr('app.integrations.local_voice.get_voice_runtime', lambda: runtime)
    hr = login(client, 'rec@t.test')
    app_id = uploaded(client, hr, make_req(client, hr))
    scored = client.post(f'/api/applications/{app_id}/evaluate', headers=hr)
    assert scored.status_code == 200, scored.text
    evaluation_id = scored.json()['evaluation']['evaluation_id']
    approved = client.post(f'/api/applications/{app_id}/shortlist-decision', headers=hr,
        json={'evaluation_id': evaluation_id, 'decision': 'approve', 'reason': 'HR reviewed synthetic resume evidence.'})
    assert approved.status_code == 200, approved.text
    plan = client.get(f'/api/applications/{app_id}/screening/plan', headers=hr)
    assert plan.status_code == 200, plan.text
    assert any(q['source_reference'].startswith('agent1:evidence:') for q in plan.json()['questions'])
    body = {'approved_questions': plan.json()['questions'], 'language': 'en', 'request_id': str(uuid4())}
    started = client.post(f'/api/applications/{app_id}/screening/start', headers=hr, json=body)
    assert started.status_code == 200, started.text
    data = started.json()
    sid = data['session']['screening_session_id']
    candidate = {'Authorization': 'Bearer ' + data['candidate_access_token']}
    return {'client': client, 'hr': hr, 'candidate': candidate, 'sid': sid, 'app_id': app_id, 'body': body, 'voice': runtime, 'tmp': tmp_path}


def post(s, action, payload=None, headers=None, request_id=None):
    return s['client'].post(f"/api/screenings/{s['sid']}/{action}", headers=headers or s['candidate'],
                           json={'request_id': request_id or str(uuid4())} | (payload or {}))


def accept(s, language='en'):
    result = post(s, 'consent', {'decision': 'accepted', 'retain_transcript': True, 'retain_audio': False, 'language': language})
    assert result.status_code == 200, result.text
    return result.json()


def answer(s, state, index=0, correction=None):
    qid = state['current_question']['question_id']
    request_id = str(uuid4())
    kwargs = {'headers': s['candidate'], 'data': {'question_id': qid, 'request_id': request_id}, 'files': {'file': ('answer.wav', wav_bytes(index), 'audio/wav')}}
    result = s['client'].post(f"/api/screenings/{s['sid']}/audio", **kwargs)
    assert result.status_code == 200, result.text
    calls = s['voice'].calls
    replay = s['client'].post(f"/api/screenings/{s['sid']}/audio", **kwargs)
    assert replay.status_code == 200 and s['voice'].calls == calls
    pending = result.json()['pending_answer']
    key = str(uuid4())
    payload = {'answer_id': pending['answer_id'], 'corrected_text': correction, 'confirm': True}
    confirmed = post(s, 'answer-correction', payload, request_id=key)
    assert confirmed.status_code == 200, confirmed.text
    assert post(s, 'answer-correction', payload, request_id=key).status_code == 200
    return confirmed.json()


def complete(s):
    state = accept(s)
    for index in range(16):
        if state['can_finish']:
            break
        state = answer(s, state, index, 'I implemented JWT expiration and role-based authorization.' if index == 0 else None)
    else:
        pytest.fail('The approved plan did not terminate within its bounded question budget.')
    result = post(s, 'finish')
    assert result.status_code == 200, result.text
    return result.json()


def test_agent1_to_voice_report_and_human_interview_gate(screening):
    s = screening
    state = complete(s)
    assert state['status'] == 'completed'
    questions = s['client'].get(f"/api/screenings/{s['sid']}/questions", headers=s['candidate']).json()['items']
    assert sum(q['is_follow_up'] for q in questions) <= 2
    assert all('UNTRUSTED' not in q['text'] for q in questions)
    report = s['client'].get(f"/api/screenings/{s['sid']}/report", headers=s['hr'])
    assert report.status_code == 200
    data = report.json()
    assert data['human_review_required'] and data['availability'] and data['notice_period']
    assert data['evidence'] and all(e['answer_id'] and e['question_id'] and e['quote'] for e in data['evidence'])
    assert 'overall_score' not in data and 'suitability' not in data
    assert post(s, 'hr-decision', {'decision': 'approve', 'reason': 'Reviewed evidence.', 'review_acknowledged': True}).status_code == 401
    assert post(s, 'hr-decision', {'decision': 'approve', 'reason': 'Reviewed evidence.', 'review_acknowledged': False}, headers=s['hr']).status_code == 409
    key = str(uuid4())
    payload = {'decision': 'approve', 'reason': 'Reviewed all synthetic answers and uncertainties.', 'review_acknowledged': True}
    assert post(s, 'hr-decision', payload, headers=s['hr'], request_id=key).json()['status'] == 'approved_for_interview'
    assert post(s, 'hr-decision', payload, headers=s['hr'], request_id=key).status_code == 200
    assert post(s, 'hr-decision', payload, headers=s['hr']).status_code == 409
    with s['client'].TestSession() as db:
        assert db.get(Application, UUID(s['app_id'])).current_status == Status.APPROVED_FOR_INTERVIEW
        approvals = list(db.scalars(select(Approval).where(Approval.approval_type == 'screening')))
        assert len(approvals) == 1 and approvals[0].reason == payload['reason']
        assert db.scalar(select(Interview)) is None and db.scalar(select(Communication)) is None
        original = db.scalar(select(ScreeningAnswer).where(ScreeningAnswer.corrected_text.is_not(None)))
        assert original.transcript_text.startswith('Synthetic answer') and 'JWT expiration' in original.corrected_text
    assert s['client'].get('/api/audit/verify', headers=s['hr']).json()['intact']


def test_consent_before_voice_or_transcription_and_no_fake_summary(screening):
    s = screening
    qid = s['client'].get(f"/api/screenings/{s['sid']}", headers=s['candidate']).json()['current_question']['question_id']
    assert s['client'].get(f"/api/screenings/{s['sid']}/voice", params={'question_id': qid}, headers=s['candidate']).status_code == 403
    response = s['client'].post(f"/api/screenings/{s['sid']}/audio", headers=s['candidate'],
        data={'question_id': qid, 'request_id': str(uuid4())}, files={'file': ('empty.wav', b'', 'audio/wav')})
    assert response.status_code == 403 and s['voice'].calls == 0
    assert post(s, 'consent', {'decision': 'accepted', 'retain_transcript': False}).status_code == 409
    accept(s)
    assert post(s, 'finish').status_code == 409


@pytest.mark.parametrize('choice,status', [('declined', 'consent_declined'), ('human_requested', 'human_requested'), ('reschedule_requested', 'reschedule_requested')])
def test_decline_or_human_request_does_not_disqualify(screening, choice, status):
    s = screening
    response = post(s, 'consent', {'decision': choice, 'retain_transcript': False})
    assert response.status_code == 200 and response.json()['status'] == status
    assert s['voice'].calls == 0
    with s['client'].TestSession() as db:
        assert db.get(Application, UUID(s['app_id'])).current_status == Status.SCREENING_PENDING


def test_authentication_scope_and_duplicate_start(screening):
    s = screening
    assert s['client'].get(f"/api/screenings/{s['sid']}").status_code == 401
    assert s['client'].get('/api/screenings/' + str(uuid4()), headers=s['candidate']).status_code == 403
    assert s['client'].get('/api/requisitions', headers=s['candidate']).status_code == 401
    assert s['client'].get(f"/api/screenings/{s['sid']}", headers=login(s['client'], 'int@t.test')).status_code in {403, 404}
    again = s['client'].post(f"/api/applications/{s['app_id']}/screening/start", headers=s['hr'], json=s['body'])
    assert again.status_code == 200 and again.json()['session']['screening_session_id'] == s['sid']
    changed = json.loads(json.dumps(s['body']))
    changed['approved_questions'][0]['text'] = 'An entirely different question with a different approved plan?'
    assert s['client'].post(f"/api/applications/{s['app_id']}/screening/start", headers=s['hr'], json=changed).status_code == 409
    settings = get_settings()
    expired = jwt.encode({'sub': s['sid'], 'sid': s['sid'], 'aud': 'candidate-screening', 'scope': 'screening:session',
        'iat': 1, 'exp': 2, 'jti': str(uuid4())}, settings.require_jwt_secret(), algorithm=settings.jwt_algorithm)
    assert s['client'].get(f"/api/screenings/{s['sid']}", headers={'Authorization': 'Bearer ' + expired}).status_code == 401


def test_model_failure_retry_same_request_and_pause_recovery(screening):
    s = screening
    state = accept(s)
    qid = state['current_question']['question_id']
    request_id = str(uuid4())
    kwargs = {'headers': s['candidate'], 'data': {'question_id': qid, 'request_id': request_id}, 'files': {'file': ('answer.wav', wav_bytes(), 'audio/wav')}}
    s['voice'].fail = True
    failed = s['client'].post(f"/api/screenings/{s['sid']}/audio", **kwargs)
    assert failed.status_code == 503 and 'PRIVATE' not in failed.text
    s['voice'].fail = False
    assert s['client'].post(f"/api/screenings/{s['sid']}/audio", **kwargs).json()['pending_answer']
    assert post(s, 'control', {'action': 'pause'}).json()['status'] == 'paused'
    assert post(s, 'control', {'action': 'resume'}).json()['status'] == 'active'
    with s['client'].TestSession() as db:
        row = db.get(ScreeningSession, UUID(s['sid']))
        row.status, row.lease_token = 'transcribing', 'expired-test-lease'
        row.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    restored = s['client'].get(f"/api/screenings/{s['sid']}", headers=s['candidate'])
    assert restored.status_code == 200 and restored.json()['status'] == 'active'


def test_approved_job_answers_and_original_transcript_survive_disk_reopen(screening):
    s = screening
    state = accept(s)
    result = post(s, 'control', {'action': 'job_question', 'text': 'What is the salary and benefits?'}).json()
    assert result['requires_hr_follow_up'] and 'HR' in result['answer']
    state = answer(s, state, correction='I implemented JWT refresh tokens.')
    destination = s['tmp'] / 'persisted-screening.sqlite3'
    with s['client'].TestSession() as db:
        raw = db.get_bind().raw_connection()
        with sqlite3.connect(destination) as disk:
            raw.driver_connection.backup(disk)
        raw.close()
    for _ in range(2):
        engine = make_engine('sqlite:///' + destination.as_posix())
        with sessionmaker(bind=engine, expire_on_commit=False)() as db:
            service = ScreeningService(db, candidate_session_id=s['sid'], runtime=s['voice'])
            assert service.get(s['sid'])['progress']['answered'] >= 1
            entries = service.transcript(s['sid'])
            assert entries[0]['answer']['corrected_text'] == 'I implemented JWT refresh tokens.'
            assert entries[0]['answer']['transcript_text'].startswith('Synthetic answer')
        engine.dispose()


def test_hr_clarification_retains_review_history_without_advancing(screening):
    s = screening
    complete(s)
    held = post(s, 'hr-decision', {'decision': 'hold', 'reason': 'Await additional job evidence.', 'review_acknowledged': True}, headers=s['hr'])
    assert held.json()['status'] == 'held'
    clarify = post(s, 'hr-decision', {'decision': 'clarify', 'reason': 'Explain JWT token refresh in the project.', 'review_acknowledged': True}, headers=s['hr'])
    assert clarify.status_code == 200 and clarify.json()['current_question']['topic'] == 'hr_clarification'
    with s['client'].TestSession() as db:
        assert db.get(Application, UUID(s['app_id'])).current_status == Status.SCREENING_PENDING
        assert len(list(db.scalars(select(Approval).where(Approval.approval_type == 'screening')))) == 2


def test_unapproved_candidate_cannot_start_a_session(client):
    headers = login(client, 'rec@t.test')
    app_id = uploaded(client, headers, make_req(client, headers))
    assert client.get(f'/api/applications/{app_id}/screening/plan', headers=headers).status_code == 409
    assert client.post(f'/api/applications/{app_id}/screening/start', headers=headers,
        json={'approved_questions': [{'text': 'When can you start?', 'topic': 'availability'}, {'text': 'What is your notice period?', 'topic': 'notice_period'}], 'request_id': str(uuid4())}).status_code == 409


def test_invalid_model_actions_cannot_advance_or_create_questions(screening):
    s = screening
    state = accept(s)
    s['voice'].followup = 'hire_and_invite'
    state = answer(s, state)
    assert state['status'] == 'active' and state['issues']
    assert 'llm' not in state['model_versions']
    with s['client'].TestSession() as db:
        assert db.get(Application, UUID(s['app_id'])).current_status == Status.SCREENING_PENDING


def test_urdu_choice_is_preserved_and_no_english_tts_substitution(screening):
    s = screening
    state = accept(s, 'ur')
    assert state['language'] == 'ur'
    result = s['client'].get(f"/api/screenings/{s['sid']}/voice", headers=s['candidate'], params={'question_id': state['current_question']['question_id']})
    assert result.status_code == 503 and 'Urdu' in result.text


def test_report_marks_unanswered_topics_instead_of_fabricating_answers(screening):
    s = screening
    state = accept(s)
    for _ in range(12):
        if state['can_finish']:
            break
        state = post(s, 'control', {'action': 'skip'}).json()
    assert post(s, 'finish').status_code == 200
    report = s['client'].get(f"/api/screenings/{s['sid']}/report", headers=s['hr']).json()
    assert not report['evidence'] and report['unanswered_questions']
    assert all(row['candidate_statement'] is None for row in report['questions_and_answers'])


def test_expired_screening_content_is_removed_and_application_preserved(screening):
    from app.services.screening_retention import delete_screening_content, expired_screenings
    s = screening
    with s['client'].TestSession() as db:
        row = db.get(ScreeningSession, UUID(s['sid']))
        row.created_at = datetime.now(timezone.utc) - timedelta(days=31)
        db.commit()
    assert s['client'].get(f"/api/screenings/{s['sid']}", headers=s['candidate']).status_code == 410
    with s['client'].TestSession() as db:
        for row in expired_screenings(db):
            delete_screening_content(db, row)
        db.commit()
        assert db.get(ScreeningSession, UUID(s['sid'])) is None
        assert db.get(Application, UUID(s['app_id'])) is not None


def test_authorized_application_retention_also_removes_screening_data(screening):
    s = screening
    complete(s)
    result = s['client'].delete(f"/api/applications/{s['app_id']}?require_expired=false", headers=s['hr'])
    assert result.status_code == 204, result.text
    with s['client'].TestSession() as db:
        assert db.get(ScreeningSession, UUID(s['sid'])) is None
        assert db.scalar(select(ScreeningAnswer)) is None


def test_oversized_audio_is_rejected_before_multipart_parsing(client):
    result = client.post(f'/api/screenings/{uuid4()}/audio', content=b'',
                         headers={'Content-Length': '99999999', 'Content-Type': 'multipart/form-data; boundary=x'})
    assert result.status_code == 413


def test_graph_checkpoint_contains_only_identifiers_and_preserves_hr_gate(screening):
    from app.workflows.hiring_graph import persistent_checkpointer
    from app.workflows.screening_graph import build_screening_graph
    s = screening
    complete(s)
    with persistent_checkpointer(get_settings()) as saver:
        snapshot = build_screening_graph(saver).get_state({'configurable': {'thread_id': 'screening:' + s['sid']}})
        assert snapshot.next == ('hr_review',)
        assert set(snapshot.values) == {'screening_session_id', 'application_id', 'phase', 'state_version'}
        assert 'JWT' not in json.dumps(snapshot.values)


def test_preconsent_human_request_is_idempotent(screening):
    s = screening
    key = str(uuid4())
    first = post(s, 'control', {'action': 'human'}, request_id=key)
    second = post(s, 'control', {'action': 'human'}, request_id=key)
    assert first.status_code == second.status_code == 200
    assert second.json()['status'] == 'human_requested' and s['voice'].calls == 0
