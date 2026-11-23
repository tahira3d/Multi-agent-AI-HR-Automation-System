from sqlalchemy import select
from tests.test_api_phase1 import client as client, login, make_req
from app.database.models import RubricVersion


def test_no_op_edit_preserves_rubric_version(client):
    headers = login(client, 'rec@t.test')
    identifier = make_req(client, headers)
    original = client.get(f'/api/requisitions/{identifier}', headers=headers).json()
    response = client.patch(f'/api/requisitions/{identifier}', headers=headers,
                            json={'job_title': original['job_title'], 'expected_rubric_version': 1})
    assert response.status_code == 200
    assert response.json()['rubric_version'] == 1
    with client.TestSession() as db:
        assert len(db.scalars(select(RubricVersion)).all()) == 1


def test_clients_cannot_inject_approval_status_or_owner(client):
    headers = login(client, 'rec@t.test')
    body = {'job_title': 'Backend developer', 'job_description': 'Develop tested Python APIs and PostgreSQL data models.',
            'current_status': 'Approved for Screening'}
    assert client.post('/api/requisitions', headers=headers, json=body).status_code == 422
