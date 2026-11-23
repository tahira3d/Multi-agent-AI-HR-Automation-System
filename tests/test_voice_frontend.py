"""Actual Streamlit rendering with HTTP/audio mocked; physical microphone testing is separate."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4
import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
SID, QID = str(uuid4()), str(uuid4())
TOKEN = 'synthetic-candidate-scoped-token'


def state(**updates):
    return {'screening_session_id': SID, 'status': 'consent_requested', 'consent_status': 'requested', 'retain_transcript': False,
        'language': 'en', 'current_question': {'question_id': QID, 'text': 'Explain your FastAPI project.'},
        'progress': {'answered': 0, 'total': 3}, 'pending_answer': None, 'can_finish': False, **updates}


def transport(payload, calls):
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        assert url.endswith('/api/screenings/' + SID)
        assert kwargs['headers']['Authorization'] == 'Bearer ' + TOKEN
        return SimpleNamespace(ok=True, status_code=200, content=b'json', json=lambda: payload)
    return request


@pytest.mark.parametrize('payload', [state(), state(status='consent_declined', consent_status='declined'),
    state(status='paused', consent_status='accepted', retain_transcript=True), state(status='completed', consent_status='accepted', retain_transcript=True)])
def test_no_microphone_capture_before_consent_or_inactive_state(payload):
    calls = []
    microphone = Mock(return_value=None)
    with patch('requests.request', side_effect=transport(payload, calls)), patch('streamlit.audio_input', microphone), patch('frontend.pages.voice_screening._voice', side_effect=AssertionError('No TTS before consent')):
        app = AppTest.from_string(f'from frontend.pages.voice_screening import render\nrender("{SID}","{TOKEN}")', default_timeout=20).run()
        assert not app.exception
        microphone.assert_not_called()


def test_confirmed_consent_plays_question_and_enables_microphone():
    payload = state(status='active', consent_status='accepted', retain_transcript=True)
    microphone = Mock(return_value=None)
    calls = []
    with patch('requests.request', side_effect=transport(payload, calls)), patch('streamlit.audio_input', microphone), patch('frontend.pages.voice_screening._voice', return_value=b'fake-wave') as spoken:
        app = AppTest.from_string(f'from frontend.pages.voice_screening import render\nrender("{SID}","{TOKEN}")', default_timeout=20).run()
        assert not app.exception
        spoken.assert_called_once()
        microphone.assert_called_once()
        assert microphone.call_args.kwargs['sample_rate'] == 16000


def test_root_candidate_route_never_enters_hr_navigation():
    calls = []
    with patch('requests.request', side_effect=transport(state(), calls)), patch('streamlit.audio_input', side_effect=AssertionError('Consent missing')):
        app = AppTest.from_file(str(ROOT / 'frontend' / 'app.py'), default_timeout=20)
        app.query_params = {'screening': SID, 'access': TOKEN}
        app.session_state['access_token'] = 'existing-hr-token-must-not-be-used'
        app.run()
        assert not app.exception
        assert any(title.value == 'Candidate voice screening' for title in app.title)
        assert not any('Overview' in str(link) for link in app.sidebar)
        assert 'access' not in app.query_params
        assert all('/auth/me' not in url for _, url, _ in calls)


def test_hr_screening_page_requires_review_acknowledgment_before_decision():
    payload = state(status='completed', consent_status='accepted', retain_transcript=True, report_available=True, issues=[])
    posts = []
    def request(method, url, **kwargs):
        if method == 'POST':
            posts.append((url, kwargs['json']))
            payload['status'] = 'approved_for_interview'
            value = payload
        elif url.endswith('/eligible'):
            value = {'items': []}
        elif url.endswith('/api/screenings'):
            value = {'items': [payload]}
        elif url.endswith('/report'):
            value = {'evidence': [], 'summary': 'Synthetic report for UI controls only.'}
        elif url.endswith('/transcript'):
            value = {'items': []}
        else:
            value = payload
        return SimpleNamespace(ok=True, status_code=200, content=b'json', json=lambda: value)
    wrapper = "import streamlit as st\nst.session_state['access_token']='synthetic-hr'\nfrom frontend.pages.screening_review import render\nrender()"
    with patch('requests.request', side_effect=request):
        app = AppTest.from_string(wrapper, default_timeout=20).run()
        assert not app.exception
        button = next(button for button in app.button if button.label == 'Record HR decision')
        assert not button.disabled and not posts
        button.click().run()
        assert app.warning and not posts
        app.text_area[0].set_value('Reviewed all synthetic answers and uncertainties.')
        app.checkbox[0].check()
        next(button for button in app.button if button.label == 'Record HR decision').click().run()
        assert not app.exception
        assert len(posts) == 1 and posts[0][1]['review_acknowledged'] is True
