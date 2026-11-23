"""Overview endpoint regression checks use real settings and an isolated database."""
import pytest
from sqlalchemy import select

from app.database.models import User
from tests.test_api_phase1 import client as client, login, make_req, SAMPLES


@pytest.mark.parametrize('configured', [True, False])
def test_dashboard_uses_groq_settings_and_preserves_owned_metrics(client, monkeypatch, configured):
    key = 'synthetic-dashboard-groq-key' if configured else ''
    monkeypatch.setenv('GROQ_API_KEY', key)
    monkeypatch.setenv('GROQ_BASE_URL', 'https://api.groq.com/openai/v1')
    monkeypatch.setenv('GROQ_MODEL', 'openai/gpt-oss-120b')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', 'synthetic-unused-azure-key')
    headers = login(client, 'rec@t.test')
    with client.TestSession() as db:
        recruiter = db.scalar(select(User).where(User.email == 'rec@t.test'))
        db.add(User(name='Other recruiter', email='other@t.test', role='recruiter',
                    password_hash=recruiter.password_hash))
        db.commit()

    def upload(headers):
        requisition_id = make_req(client, headers)
        resume = SAMPLES / '01_ayesha_raza.pdf'
        result = client.post(f'/api/requisitions/{requisition_id}/resumes', headers=headers,
            files=[('files', (resume.name, resume.read_bytes(), 'application/pdf'))])
        assert result.status_code == 201, result.text
        return result.json()['results'][0]['application_id']

    own_application = upload(headers)
    other_application = upload(login(client, 'other@t.test'))
    result = client.get('/api/dashboard', headers=headers)
    assert result.status_code == 200, result.text
    data = result.json()
    assert data['evaluation_available'] is configured
    assert {name: data[name] for name in ('total_requisitions', 'total_resumes', 'total_evaluated',
        'awaiting_review', 'approved_for_screening')} == {
            'total_requisitions': 1, 'total_resumes': 1, 'total_evaluated': 0,
            'awaiting_review': 0, 'approved_for_screening': 0}
    assert data['recent_activity']
    assert any(event['application_id'] == own_application for event in data['recent_activity'])
    assert all(event['application_id'] in (None, own_application) for event in data['recent_activity'])
    assert other_application not in result.text
    assert 'synthetic-dashboard-groq-key' not in result.text
    assert 'synthetic-unused-azure-key' not in result.text


def test_dashboard_requires_authentication(client):
    assert client.get('/api/dashboard').status_code == 401
