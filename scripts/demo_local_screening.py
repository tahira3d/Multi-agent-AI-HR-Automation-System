"""Reproduce isolated fixed/adaptive screenings with real local STT/TTS/Ollama.

Only synthetic candidates are used. Agent 1's provider is explicitly mocked;
its existing parser, scoring, API, shortlist and source-validation logic run.
No hosted API or live database is used. HR decisions are simulated only when
--simulate-hr is explicitly selected. Microphone hardware is tested separately.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import time
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, default=ROOT / 'storage' / 'models')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'storage' / 'evaluations' / ('screening-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')))
    parser.add_argument('--simulate-hr', action='store_true')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    database = output / 'synthetic-screening.sqlite3'
    if database.exists():
        raise RuntimeError('Use a new output folder; an existing demonstration database will not be overwritten.')
    os.environ.update({'DATABASE_URL': 'sqlite:///' + database.as_posix(), 'STORAGE_DIR': str(output / 'resumes'),
        'CHECKPOINT_SQLITE_PATH': str(output / 'checkpoints.sqlite3'), 'JWT_SECRET_KEY': secrets.token_urlsafe(48),
        'WHISPER_MODEL_PATH': str(args.models_dir.resolve() / 'whisper-base'),
        'PIPER_VOICE_PATH': str(args.models_dir.resolve() / 'piper' / 'en_US-lessac-medium.onnx'),
        'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
    original_connect = socket.socket.connect
    def loopback_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            raise OSError('The local demonstration forbids non-loopback network access.')
        return original_connect(sock, address)
    socket.socket.connect = loopback_connect
    sys.path.insert(0, str(ROOT / 'backend'))
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from app import main as application
    from app.agents.resume_agent import ResumeScoringAgent
    from app.core.security import hash_password
    from app.database.connection import Base, SessionLocal, engine
    from app.database.models import User
    from app.integrations.local_voice import get_voice_runtime
    from app.schemas.evaluation import LLMEvaluation
    from scripts.measure_voice import peak_process_mb, words

    class SyntheticResumeProvider:
        def generate(self, messages, validator=None):
            payload = json.loads(messages[1]['content'])
            line = next(line for line in payload['resume_text'].splitlines() if 'FastAPI' in line)
            label = re.match(r'\[(L\d+)\]', line).group(1)
            value = LLMEvaluation(criteria=[{'key': item['key'], 'score': 80, 'finding': 'explicit',
                'explanation': 'Synthetic fixture demonstrates the scoring contract; this is not a live Agent 1 model result.',
                'evidence': [{'source_text': '[[' + label + ']]', 'explanation': 'Synthetic parsed source.', 'evidence_type': 'explicit'}]}
                for item in payload['rubric']['criteria']], missing_or_unclear_items=['Clarify the project contribution and current notice period.'],
                recommendation_for_human_review='Synthetic fixture requires human review.')
            if validator:
                validator(value)
            return value, 'synthetic-fixture-provider-not-live-agent1'

    Base.metadata.create_all(engine)
    password = secrets.token_urlsafe(24)
    with SessionLocal() as db:
        db.add(User(name='Synthetic HR Reviewer', email='voice-hr@synthetic.test', role='recruiter', password_hash=hash_password(password)))
        db.commit()
    client = TestClient(application.app)
    login = client.post('/api/auth/login', json={'email': 'voice-hr@synthetic.test', 'password': password})
    assert login.status_code == 200
    hr = {'Authorization': 'Bearer ' + login.json()['access_token']}
    runtime = get_voice_runtime()
    result = {'source': 'synthetic_local_voice_demonstration', 'agent1_provider': 'explicit_mock; real scoring/parser/approval API',
        'hr_review': 'simulated_authenticated_fixture' if args.simulate_hr else 'pending_real_hr_review',
        'physical_microphone': 'not_tested_by_this_script', 'network_policy': 'Python sockets restricted to loopback',
        'scenarios': [], 'runtime': runtime.status()}

    def checked(response):
        if response.status_code >= 400:
            raise RuntimeError(f'Demonstration API failed: HTTP {response.status_code}; {response.json().get("detail", "request failed")}')
        return response.json()

    for mode, budget in [('fixed', 0), ('adaptive', 2)]:
        os.environ['SCREENING_MAX_FOLLOWUPS'] = str(budget)
        job = checked(client.post('/api/requisitions', headers=hr, json={'job_title': f'Synthetic Python Developer ({mode})',
            'job_description': 'Build Python and FastAPI services using PostgreSQL. Implement authentication, request validation and authorization tests. Explain your own project contributions.',
            'required_skills': ['Python', 'FastAPI', 'PostgreSQL'], 'work_arrangement': 'remote',
            'screening_questions': ['Describe your own contribution to the FastAPI project in your resume.'],
            'rubric': {'criteria': [{'key': 'required_skills', 'label': 'Required backend skills', 'weight': 100}]}}))
        sample = ROOT / 'sample_data' / 'synthetic_resumes' / '01_ayesha_raza.pdf'
        upload = checked(client.post(f"/api/requisitions/{job['requisition_id']}/resumes", headers=hr,
            files={'files': (sample.name, sample.read_bytes(), 'application/pdf')}))
        app_id = upload['results'][0]['application_id']
        with patch('app.agents.resume_agent.get_resume_agent', return_value=ResumeScoringAgent(SyntheticResumeProvider())):
            evaluation = checked(client.post(f'/api/applications/{app_id}/evaluate', headers=hr))['evaluation']
        checked(client.post(f'/api/applications/{app_id}/shortlist-decision', headers=hr,
            json={'evaluation_id': evaluation['evaluation_id'], 'decision': 'approve', 'reason': 'Explicit synthetic HR fixture approval for the isolated demonstration.'}))
        plan = checked(client.get(f'/api/applications/{app_id}/screening/plan', headers=hr))
        started = checked(client.post(f'/api/applications/{app_id}/screening/start', headers=hr,
            json={'approved_questions': plan['questions'], 'language': 'en', 'request_id': str(uuid4())}))
        sid = started['session']['screening_session_id']
        candidate = {'Authorization': 'Bearer ' + started['candidate_access_token']}
        state = checked(client.post(f'/api/screenings/{sid}/consent', headers=candidate,
            json={'decision': 'accepted', 'retain_transcript': True, 'retain_audio': False, 'language': 'en', 'request_id': str(uuid4())}))
        turns = []
        for index in range(16):
            if state['can_finish']:
                break
            question = state['current_question']
            start = time.perf_counter()
            question_audio = client.get(f'/api/screenings/{sid}/voice', headers=candidate, params={'question_id': question['question_id']})
            if question_audio.status_code != 200 or question_audio.content[:4] != b'RIFF':
                raise RuntimeError('Real question speech playback failed.')
            topic = question['topic']
            if topic == 'availability':
                statement = 'I am available to start this role in four weeks.'
            elif topic == 'notice_period':
                statement = 'My current notice period is four weeks.'
            elif question['is_follow_up']:
                statement = 'I implemented authentication using short lived access tokens. I tested token expiration and role based authorization.'
            else:
                statement = 'I used Python for that project.'
            audio = runtime.synthesize(statement)
            pending_state = checked(client.post(f'/api/screenings/{sid}/audio', headers=candidate,
                data={'question_id': question['question_id'], 'request_id': str(uuid4())}, files={'file': ('synthetic-answer.wav', audio, 'audio/wav')}))
            pending = pending_state['pending_answer']
            correction = statement if words(pending['transcript_text']) != words(statement) else None
            state = checked(client.post(f'/api/screenings/{sid}/answer-correction', headers=candidate,
                json={'answer_id': pending['answer_id'], 'corrected_text': correction, 'confirm': True, 'request_id': str(uuid4())}))
            turns.append({'question_id': question['question_id'], 'topic': topic, 'follow_up': question['is_follow_up'],
                          'latency_seconds': round(time.perf_counter() - start, 4), 'synthetic_candidate_correction': correction is not None})
        else:
            raise RuntimeError('The screening exceeded the bounded question budget.')
        finished = checked(client.post(f'/api/screenings/{sid}/finish', headers=candidate, json={'request_id': str(uuid4())}))
        report = checked(client.get(f'/api/screenings/{sid}/report', headers=hr))
        transcript = checked(client.get(f'/api/screenings/{sid}/transcript', headers=hr))['items']
        answers = {item['answer']['answer_id']: item['answer']['corrected_text'] if item['answer']['corrected_text'] is not None else item['answer']['transcript_text'] for item in transcript}
        mismatch = sum(answers.get(item['answer_id']) != item['quote'] for item in report['evidence'])
        questions = checked(client.get(f'/api/screenings/{sid}/questions', headers=candidate))['items']
        repeated = len(questions) - len({q['text'].casefold() for q in questions})
        summary = {'mode': mode, 'session_id': sid, 'base_questions': len(plan['questions']),
            'questions_asked': len(turns), 'followups': sum(t['follow_up'] for t in turns),
            'approved_question_coverage': sum(q['answered'] or bool(q['unresolved_reason']) for q in questions) / len(questions),
            'exact_repeated_question_rate': repeated / len(questions), 'unmatched_report_quote_rate': mismatch / len(report['evidence']) if report['evidence'] else None,
            'average_turn_latency_seconds': round(sum(t['latency_seconds'] for t in turns) / len(turns), 4),
            'synthetic_candidate_correction_rate': sum(t['synthetic_candidate_correction'] for t in turns) / len(turns),
            'hr_correction_rate': None, 'hr_correction_note': 'Requires a human-reviewed evaluation set; not estimated from synthetic automation.',
            'turns': turns, 'final_status': finished['status'], 'model_versions': report['model_versions']}
        (output / (mode + '-report.json')).write_text(json.dumps(report, indent=2), 'utf-8')
        (output / (mode + '-transcript.json')).write_text(json.dumps({'items': transcript}, indent=2), 'utf-8')
        if args.simulate_hr:
            decision = checked(client.post(f'/api/screenings/{sid}/hr-decision', headers=hr,
                json={'decision': 'approve', 'reason': 'Simulated HR reviewer approved this isolated synthetic fixture.', 'review_acknowledged': True, 'request_id': str(uuid4())}))
            summary['final_status'] = decision['status']
        result['scenarios'].append(summary)
        print(json.dumps(summary), flush=True)
    result['python_voice_process_peak_rss_mb'] = peak_process_mb()
    result['ollama_peak_memory'] = 'not_measured_by_this_process'
    result['status'] = 'passed'
    (output / 'results.json').write_text(json.dumps(result, indent=2), 'utf-8')
    runtime.unload()
    print('Measured local demonstration saved:', output, flush=True)


if __name__ == '__main__':
    main()
