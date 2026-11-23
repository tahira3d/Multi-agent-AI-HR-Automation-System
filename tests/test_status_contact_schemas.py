import pytest
from pydantic import ValidationError

from app.core.status import ALLOWED_TRANSITIONS, InvalidTransition, Status, assert_transition, can_transition
from app.schemas.requisition import DEFAULT_RUBRIC, RequisitionCreate, Rubric
from app.services.contact_extractor import extract_contact, name_from_filename


# ---- lifecycle -------------------------------------------------------------------------
def test_every_status_has_a_transition_entry():
    assert set(ALLOWED_TRANSITIONS) == set(Status)


def test_happy_path_is_allowed():
    path = [Status.RESUME_RECEIVED, Status.SCORED_HR_REVIEW, Status.APPROVED_FOR_SCREENING,
            Status.SCREENING_PENDING, Status.SCREENING_COMPLETED_HR_REVIEW,
            Status.APPROVED_FOR_INTERVIEW, Status.INTERVIEW_PROPOSED, Status.INTERVIEW_SCHEDULED]
    for a, b in zip(path, path[1:]):
        assert_transition(a, b)


@pytest.mark.parametrize("a,b", [
    (Status.SCORED_HR_REVIEW, Status.SCREENING_PENDING),            # skips HR approval gate 1
    (Status.SCREENING_COMPLETED_HR_REVIEW, Status.INTERVIEW_PROPOSED),  # skips gate 2
    (Status.INTERVIEW_PROPOSED, Status.RESUME_RECEIVED),
    (Status.RESUME_RECEIVED, Status.INTERVIEW_SCHEDULED),
])
def test_gate_skipping_is_blocked(a, b):
    assert not can_transition(a, b)
    with pytest.raises(InvalidTransition):
        assert_transition(a, b)


def test_closed_is_terminal():
    assert ALLOWED_TRANSITIONS[Status.CLOSED] == frozenset()


# ---- contact extraction --------------------------------------------------------------------
def test_extract_contact_from_resume_header():
    c = extract_contact("AYESHA RAZA\nBackend Engineer\nEmail: Ayesha.Raza@Example.test | Phone: +92 300 1234567\n")
    assert c.name == "Ayesha Raza"
    assert c.email == "ayesha.raza@example.test"
    assert c.phone == "+92 300 1234567"


def test_skills_heading_is_not_a_name():
    assert extract_contact("SKILLS\nPython, FastAPI\n").name is None
    assert extract_contact("Python FastAPI Docker Git Azure\n").name is None  # 5 words


def test_name_from_filename():
    assert name_from_filename("13_scanned_resume.pdf") == "Scanned Resume"
    assert name_from_filename("07_noor_fatima.pdf") == "Noor Fatima"


# ---- rubric / requisition schema (AC-01) ---------------------------------------------------
def test_default_rubric_valid_and_sums_to_100():
    assert sum(c.weight for c in Rubric(criteria=DEFAULT_RUBRIC).criteria) == 100


def test_rubric_rejects_bad_weights_and_duplicate_keys():
    with pytest.raises(ValidationError):
        Rubric(criteria=[{"key": "skills", "label": "Skills", "weight": 60}])
    with pytest.raises(ValidationError):
        Rubric(criteria=[{"key": "a1", "label": "A", "weight": 50}, {"key": "a1", "label": "B", "weight": 50}])


def test_requisition_defaults_to_standard_rubric():
    req = RequisitionCreate(job_title="Backend Developer", job_description="x" * 40)
    assert len(req.rubric.criteria) == 5


def test_requisition_requires_real_description():
    with pytest.raises(ValidationError):
        RequisitionCreate(job_title="Backend Developer", job_description="short")
