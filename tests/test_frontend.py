"""Streamlit app tests use mocked HTTP responses; no static demo path exists."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def test_frontend_login_gate_and_connected_dashboard():
    calls = []
    user = {'user_id': 'synthetic-user', 'name': 'Synthetic HR', 'full_name': 'Synthetic HR',
            'email': 'hr@synthetic.test', 'role': 'recruiter'}

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs.get('headers', {})))
        if url.endswith('/auth/login'):
            result = {'access_token': 'synthetic-http-test-token', 'role': 'recruiter'}
        elif url.endswith('/auth/me'):
            assert kwargs['headers']['Authorization'] == 'Bearer synthetic-http-test-token'
            result = user
        elif url.endswith('/dashboard'):
            result = {'total_requisitions': 3, 'total_resumes': 15, 'total_evaluated': 8,
                      'awaiting_review': 6, 'approved_for_screening': 2, 'recent_activity': []}
        else:
            raise AssertionError('Unexpected backend endpoint: ' + url)
        return SimpleNamespace(ok=True, status_code=200, content=b'json', json=lambda: result)

    with patch('requests.request', side_effect=request):
        app = AppTest.from_file(str(ROOT / 'frontend' / 'app.py'), default_timeout=20).run()
        assert not app.exception
        assert len(app.text_input) == 2
        assert not calls  # No private API is called before authentication.
        app.text_input[0].set_value('hr@synthetic.test')
        app.text_input[1].set_value('synthetic-password')
        app.button[0].click().run()
        assert not app.exception
        assert any(metric.value == '15' for metric in app.metric)
        assert app.session_state['current_user']['role'] == 'recruiter'


def test_backend_error_does_not_create_frontend_session():
    with patch('requests.request', return_value=SimpleNamespace(ok=False, status_code=401,
            json=lambda: {'detail': 'Incorrect e-mail or password'})):
        app = AppTest.from_file(str(ROOT / 'frontend' / 'app.py'), default_timeout=20).run()
        app.text_input[0].set_value('hr@synthetic.test')
        app.text_input[1].set_value('wrong-password')
        app.button[0].click().run()
        assert not app.exception
        assert app.error[0].value == 'Incorrect e-mail or password'
