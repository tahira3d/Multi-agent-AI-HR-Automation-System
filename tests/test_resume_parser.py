"""AC-02: supported files are parsed; unsupported/unreadable files are clearly flagged."""
import json
from pathlib import Path

import pytest

from app.services.resume_parser import parse_resume

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data" / "synthetic_resumes"
MANIFEST = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("entry", MANIFEST, ids=[m["file"] for m in MANIFEST])
def test_manifest_expectations(entry):
    content = (SAMPLES / entry["file"]).read_bytes()
    result = parse_resume(entry["file"], content)
    assert result.status == entry["expected_status"], result
    assert result.reason_code == entry["expected_reason"]
    if entry["expected_status"] == "parsed":
        assert len(result.text) > 100
    else:
        assert result.text == ""  # never invent extracted content
        assert result.message  # actionable message for HR
    if "expected_warning_contains" in entry:
        assert any(entry["expected_warning_contains"] in w for w in result.warnings)


def test_normal_resumes_have_no_injection_warning():
    for entry in MANIFEST:
        if entry["kind"] == "normal":
            result = parse_resume(entry["file"], (SAMPLES / entry["file"]).read_bytes())
            assert result.warnings == []


def test_duplicate_has_same_hash():
    a = parse_resume("a.pdf", (SAMPLES / "03_sana_iqbal.pdf").read_bytes())
    b = parse_resume("b.pdf", (SAMPLES / "17_duplicate_of_03.pdf").read_bytes())
    assert a.sha256 == b.sha256


def test_oversize_rejected():
    result = parse_resume("big.pdf", b"%PDF-" + b"0" * 2000, max_bytes=1000)
    assert result.status == "rejected" and result.reason_code == "file_too_large"


def test_empty_file_rejected():
    assert parse_resume("x.docx", b"").reason_code == "empty_file"


def test_extension_check_is_case_insensitive():
    content = (SAMPLES / "01_ayesha_raza.pdf").read_bytes()
    assert parse_resume("CV.PDF", content).status == "parsed"
