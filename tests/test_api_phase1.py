"""End-to-end API tests for Phase 1 (SQLite in memory, no Postgres needed).

These need FastAPI + SQLAlchemy + httpx installed (pip install -r backend/requirements.txt).
They are skipped automatically when those packages are missing.
"""
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data" / "synthetic_resumes"
JD = (Path(__file__).resolve().parents[1] / "sample_data" / "sample_job_description.txt").read_text()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "t" * 48)
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app import main
    from app.core.security import hash_password
    from app.database import connection, models

    test_engine = connection.make_engine("sqlite://")
    connection.Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[connection.get_db] = override_get_db
    with TestSession() as db:
        for name, email, role in [("Rina", "rec@t.test", "recruiter"), ("Ivan", "int@t.test", "interviewer"),
                                  ("Ada", "adm@t.test", "admin")]:
            db.add(models.User(name=name, email=email, role=role, password_hash=hash_password("Passw0rd!Passw0rd")))
        db.commit()
    c = TestClient(main.app)  # not used as a context manager: skips the Postgres lifespan hook
    c.TestSession = TestSession
    yield c
    main.app.dependency_overrides.clear()


def login(client, email):
    r = client.post("/api/auth/login", json={"email": email, "password": "Passw0rd!Passw0rd"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_req(client, headers):
    r = client.post("/api/requisitions", headers=headers,
                    json={"job_title": "Backend Python Developer", "job_description": JD})
    assert r.status_code == 201, r.text
    return r.json()["requisition_id"]


def test_auth_required_and_login_failures(client):
    assert client.get("/api/requisitions").status_code == 401
    r = client.post("/api/auth/login", json={"email": "rec@t.test", "password": "wrong"})
    assert r.status_code == 401
    assert client.post("/api/auth/login", json={"email": "nobody@t.test", "password": "x"}).status_code == 401


def test_health_metrics_cors_and_security_headers(client):
    live = client.get("/health/live", headers={"X-Request-ID": "m0-test-request"})
    assert live.status_code == 200 and live.json()["status"] == "live"
    assert live.headers["X-Request-ID"] == "m0-test-request"
    assert live.headers["X-Content-Type-Options"] == "nosniff"
    assert live.headers["X-Frame-Options"] == "DENY"
    assert live.headers["Referrer-Policy"] == "no-referrer"
    assert live.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "# TYPE http_requests_total counter" in metrics.text
    assert 'route="/health/live"' in metrics.text

    preflight = client.options("/health/live", headers={
        "Origin": "http://localhost:8501",
        "Access-Control-Request-Method": "GET",
    })
    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == "http://localhost:8501"


def test_health_metrics_and_security_headers(client):
    live = client.get("/health/live", headers={"X-Request-ID": "health-test-1"})
    assert live.status_code == 200 and live.json() == {"status": "live"}
    assert live.headers["x-request-id"] == "health-test-1"
    assert live.headers["x-content-type-options"] == "nosniff"
    assert live.headers["x-frame-options"] == "DENY"
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "http_requests_total" in metrics.text


def test_role_enforcement(client):
    interviewer = login(client, "int@t.test")
    r = client.post("/api/requisitions", headers=interviewer,
                    json={"job_title": "Backend Python Developer", "job_description": JD})
    assert r.status_code == 403
    recruiter = login(client, "rec@t.test")
    assert client.post("/api/users", headers=recruiter, json={
        "name": "X Y", "email": "x@y.test", "password": "Passw0rd!Passw0rd", "role": "admin"}).status_code == 403


def test_requisition_defaults_rubric_and_validation(client):
    h = login(client, "rec@t.test")
    rid = make_req(client, h)
    body = client.get(f"/api/requisitions/{rid}", headers=h).json()
    assert sum(c["weight"] for c in body["rubric"]["criteria"]) == 100
    bad = client.post("/api/requisitions", headers=h, json={
        "job_title": "Dev", "job_description": JD,
        "rubric": {"criteria": [{"key": "skills", "label": "Skills", "weight": 50}]}})
    assert bad.status_code == 422


def test_upload_full_synthetic_set(client):
    h = login(client, "rec@t.test")
    rid = make_req(client, h)
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream"))
             for p in sorted(SAMPLES.iterdir()) if p.name != "manifest.json"]
    r = client.post(f"/api/requisitions/{rid}/resumes", headers=h, files=files)
    assert r.status_code == 201, r.text
    body = r.json()
    by_name = {x["filename"]: x for x in body["results"]}
    assert len(body["results"]) == 20
    # 12 normal + injection = 13 accepted; scanned, encrypted, corrupt, empty docx = 4 manual review
    assert body["accepted"] == 13 and body["needs_manual_review"] == 4
    assert body["rejected"] == 2 and body["duplicates"] == 1
    assert by_name["17_duplicate_of_03.pdf"]["outcome"] == "duplicate"
    assert by_name["14_password_protected.pdf"]["reason_code"] == "password_protected"
    assert by_name["18_docx_named_pdf.pdf"]["outcome"] == "rejected"
    assert any("possible_prompt_injection" in w for w in by_name["16_prompt_injection.pdf"]["warnings"])

    apps = client.get(f"/api/requisitions/{rid}/applications", headers=h).json()
    statuses = {a["current_status"] for a in apps}
    assert statuses == {"Resume Received", "Needs Manual Review"}


def test_audit_chain_intact_then_tamper_detected(client):
    h = login(client, "rec@t.test")
    rid = make_req(client, h)
    p = SAMPLES / "01_ayesha_raza.pdf"
    client.post(f"/api/requisitions/{rid}/resumes", headers=h, files=[("files", (p.name, p.read_bytes(), "application/pdf"))])
    v = client.get("/api/audit/verify", headers=h).json()
    assert v["intact"] is True and v["events"] >= 3

    from app.database import models
    with client.TestSession() as db:
        from sqlalchemy import select
        ev = db.scalar(select(models.AuditEvent).where(models.AuditEvent.seq == 2))
        ev.action = "something_else"      # simulate tampering
        db.commit()
    v2 = client.get("/api/audit/verify", headers=h).json()
    assert v2["intact"] is False and v2["first_bad_seq"] == 2


def test_history_endpoint_has_no_resume_text(client):
    h = login(client, "rec@t.test")
    rid = make_req(client, h)
    p = SAMPLES / "01_ayesha_raza.pdf"
    up = client.post(f"/api/requisitions/{rid}/resumes", headers=h, files=[("files", (p.name, p.read_bytes(), "application/pdf"))]).json()
    app_id = up["results"][0]["application_id"]
    hist = client.get(f"/api/applications/{app_id}/history", headers=h).json()
    assert hist and hist[0]["action"] == "resume_uploaded"
    dumped = str(hist)
    assert "FastAPI" not in dumped and "example.test" not in dumped
