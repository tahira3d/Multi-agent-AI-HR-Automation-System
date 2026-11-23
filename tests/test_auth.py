from uuid import UUID
from datetime import datetime, timedelta, timezone
from tests.test_api_phase1 import client as client, login
from app.database.models import RefreshToken, User


def test_admin_creates_validated_recruiter_and_duplicate_is_safe(client):
    headers = login(client, 'adm@t.test')
    body = {'full_name': 'Synthetic New Recruiter', 'email': 'new@synthetic.test',
            'password': 'SyntheticPassword!2026', 'role': 'recruiter'}
    response = client.post('/api/users', headers=headers, json=body)
    assert response.status_code == 201, response.text
    assert 'password_hash' not in response.text
    assert response.json()['full_name'] == 'Synthetic New Recruiter'
    assert client.post('/api/users', headers=headers, json=body).status_code == 409
    assert client.post('/api/auth/login', json={'email': body['email'], 'password': body['password']}).status_code == 200


def test_inactive_account_invalidates_already_issued_token(client):
    headers = login(client, 'rec@t.test')
    identity = client.get('/api/auth/me', headers=headers).json()
    with client.TestSession() as db:
        db.get(User, UUID(identity['user_id'])).is_active = False
        db.commit()
    assert client.get('/api/auth/me', headers=headers).status_code == 401
    assert client.post('/api/auth/login', json={'email': 'rec@t.test', 'password': 'Passw0rd!Passw0rd'}).status_code == 401


def test_failed_logins_lock_account_until_backoff_expires(client):
    for _ in range(5):
        response = client.post('/api/auth/login', json={'email': 'rec@t.test', 'password': 'wrong-password'})
        assert response.status_code == 401
        assert response.json()['detail'] == 'Incorrect e-mail or password'
    assert client.post('/api/auth/login', json={
        'email': 'rec@t.test', 'password': 'Passw0rd!Passw0rd'}).status_code == 401
    with client.TestSession() as db:
        user = db.query(User).filter_by(email='rec@t.test').one()
        assert user.failed_attempts == 5
        assert user.locked_until is not None
        user.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    assert client.post('/api/auth/login', json={
        'email': 'rec@t.test', 'password': 'Passw0rd!Passw0rd'}).status_code == 200
    with client.TestSession() as db:
        user = db.query(User).filter_by(email='rec@t.test').one()
        assert user.failed_attempts == 0
        assert user.locked_until is None


def test_refresh_tokens_rotate_and_are_revoked_on_logout(client):
    login_response = client.post('/api/auth/login', json={
        'email': 'rec@t.test', 'password': 'Passw0rd!Passw0rd'})
    assert login_response.status_code == 200
    first = login_response.json()
    with client.TestSession() as db:
        stored = db.query(RefreshToken).one()
        assert stored.token_hash != first['refresh_token']
        assert len(stored.token_hash) == 64

    rotated = client.post('/api/auth/refresh', json={'refresh_token': first['refresh_token']})
    assert rotated.status_code == 200
    second = rotated.json()
    assert second['refresh_token'] != first['refresh_token']
    assert client.post('/api/auth/refresh', json={
        'refresh_token': first['refresh_token']}).status_code == 401
    assert client.post('/api/auth/logout', headers={
        'Authorization': f"Bearer {second['access_token']}"}).status_code == 204
    assert client.post('/api/auth/refresh', json={
        'refresh_token': second['refresh_token']}).status_code == 401
