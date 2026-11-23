"""Private file isolation and authorized retention deletion."""
from pathlib import Path
from tests.test_api_phase1 import client as client, login, make_req


def test_authorized_deletion_clears_application_and_preserves_audit_integrity(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CHECKPOINT_SQLITE_PATH", str(tmp_path / "checkpoints.sqlite3"))
    headers = login(client, "rec@t.test")
    req_id = make_req(client, headers)
    path = Path(__file__).resolve().parents[1] / "sample_data" / "synthetic_resumes" / "01_ayesha_raza.pdf"
    response = client.post(f"/api/requisitions/{req_id}/resumes", headers=headers,
                           files=[("files", ("../../unsafe.pdf", path.read_bytes(), "application/pdf"))])
    app_id = response.json()["results"][0]["application_id"]
    assert client.delete(f"/api/applications/{app_id}", headers=headers).status_code == 409
    assert client.delete(f"/api/applications/{app_id}?require_expired=false", headers=headers).status_code == 204
    assert client.get(f"/api/applications/{app_id}/history", headers=headers).status_code == 404
    assert client.get(f"/api/requisitions/{req_id}/applications", headers=headers).json() == []
    assert client.get("/api/audit/verify", headers=headers).json()["intact"]


def test_mime_mismatch_is_rejected_without_application(client):
    headers = login(client, "rec@t.test")
    req_id = make_req(client, headers)
    path = Path(__file__).resolve().parents[1] / "sample_data" / "synthetic_resumes" / "01_ayesha_raza.pdf"
    response = client.post(f"/api/requisitions/{req_id}/resumes", headers=headers,
                           files=[("files", ("resume.pdf", path.read_bytes(), "text/html"))])
    assert response.status_code == 201
    assert response.json()["results"][0]["reason_code"] == "mime_mismatch"
    assert client.get(f"/api/requisitions/{req_id}/applications", headers=headers).json() == []
