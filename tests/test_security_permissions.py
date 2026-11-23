import time

import jwt
import pytest

from app.core.permissions import Permission, has_permission
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password

SECRET = "x" * 40


def test_password_roundtrip_and_salting():
    h1, h2 = hash_password("S3cret!pass"), hash_password("S3cret!pass")
    assert h1 != h2  # unique salts
    assert verify_password("S3cret!pass", h1)
    assert not verify_password("wrong", h1)
    assert not verify_password("S3cret!pass", "garbage")


def test_token_roundtrip():
    token = create_access_token(user_id="u1", role="recruiter", secret=SECRET)
    claims = decode_access_token(token, secret=SECRET)
    assert claims["sub"] == "u1" and claims["role"] == "recruiter"


def test_token_wrong_secret_and_expiry():
    token = create_access_token(user_id="u1", role="recruiter", secret=SECRET)
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token, secret="y" * 40)
    expired = create_access_token(user_id="u1", role="recruiter", secret=SECRET, expires_minutes=-1)
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired, secret=SECRET)


def test_unsigned_token_rejected():
    forged = jwt.encode({"sub": "u1", "role": "admin", "exp": int(time.time()) + 600}, key=None, algorithm="none")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(forged, secret=SECRET)


def test_role_permissions():
    assert has_permission("recruiter", Permission.APPROVE_SHORTLIST)
    assert not has_permission("interviewer", Permission.APPROVE_SHORTLIST)
    assert not has_permission("admin", Permission.APPROVE_INVITATION)  # admin configures, doesn't hire
    assert not has_permission("nonsense", Permission.VIEW_CANDIDATES)
