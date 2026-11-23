"""Candidate lifecycle (brief section 9) and the only allowed transitions between statuses.

The API and the LangGraph workflow must both go through `assert_transition`, so a
candidate can never skip an approval gate (e.g. jump from "Scored" straight to a call).
"""

from __future__ import annotations

from enum import Enum


class Status(str, Enum):
    RESUME_RECEIVED = "Resume Received"
    NEEDS_MANUAL_REVIEW = "Needs Manual Review"
    SCORED_HR_REVIEW = "Scored — HR Review"
    APPROVED_FOR_SCREENING = "Approved for Screening"
    SCREENING_PENDING = "Screening Scheduled / Pending"
    SCREENING_COMPLETED_HR_REVIEW = "Screening Completed — HR Review"
    INTERVIEW_SCORED_HR_REVIEW = "Interview Scored - HR Review"
    ON_HOLD = "On Hold"
    APPROVED_FOR_INTERVIEW = "Approved for Interview"
    INTERVIEW_PROPOSED = "Interview Proposed"
    INTERVIEW_SCHEDULED = "Interview Scheduled"
    CLOSED = "Closed"


S = Status

ALLOWED_TRANSITIONS: dict[Status, frozenset[Status]] = {
    S.RESUME_RECEIVED: frozenset({S.SCORED_HR_REVIEW, S.NEEDS_MANUAL_REVIEW, S.ON_HOLD, S.CLOSED}),
    # HR corrects the file / re-uploads, or closes
    S.NEEDS_MANUAL_REVIEW: frozenset({S.RESUME_RECEIVED, S.SCORED_HR_REVIEW, S.ON_HOLD, S.CLOSED}),
    # Gate 1: only an HR decision moves a scored candidate to screening
    S.SCORED_HR_REVIEW: frozenset(
        {S.APPROVED_FOR_SCREENING, S.NEEDS_MANUAL_REVIEW, S.ON_HOLD, S.CLOSED, S.SCORED_HR_REVIEW}
    ),
    S.APPROVED_FOR_SCREENING: frozenset({S.SCREENING_PENDING, S.ON_HOLD, S.CLOSED}),
    S.SCREENING_PENDING: frozenset(
        {S.SCREENING_COMPLETED_HR_REVIEW, S.NEEDS_MANUAL_REVIEW, S.ON_HOLD, S.CLOSED}
    ),
    # Gate 2: HR reviews the screening before progression
    S.SCREENING_COMPLETED_HR_REVIEW: frozenset(
        {S.APPROVED_FOR_INTERVIEW, S.INTERVIEW_SCORED_HR_REVIEW, S.SCREENING_PENDING, S.ON_HOLD, S.CLOSED}
    ),
    S.INTERVIEW_SCORED_HR_REVIEW: frozenset({S.APPROVED_FOR_INTERVIEW, S.ON_HOLD, S.CLOSED}),
    S.APPROVED_FOR_INTERVIEW: frozenset(
        {S.INTERVIEW_PROPOSED, S.ON_HOLD, S.CLOSED, S.INTERVIEW_SCORED_HR_REVIEW}
    ),
    # Gate 3: invitation is only sent after approval, which moves to Scheduled
    S.INTERVIEW_PROPOSED: frozenset(
        {S.INTERVIEW_SCHEDULED, S.APPROVED_FOR_INTERVIEW, S.NEEDS_MANUAL_REVIEW, S.ON_HOLD, S.CLOSED}
    ),
    S.INTERVIEW_SCHEDULED: frozenset({S.INTERVIEW_PROPOSED, S.ON_HOLD, S.CLOSED}),  # reschedule or cancel
    S.ON_HOLD: frozenset(
        {
            S.RESUME_RECEIVED,
            S.SCORED_HR_REVIEW,
            S.APPROVED_FOR_SCREENING,
            S.SCREENING_PENDING,
            S.SCREENING_COMPLETED_HR_REVIEW,
            S.INTERVIEW_SCORED_HR_REVIEW,
            S.APPROVED_FOR_INTERVIEW,
            S.CLOSED,
        }
    ),
    S.CLOSED: frozenset({S.ON_HOLD}),
}


class InvalidTransition(Exception):
    def __init__(self, current: Status, target: Status):
        super().__init__(f"Transition '{current.value}' -> '{target.value}' is not allowed")
        self.current, self.target = current, target


def can_transition(current: str | Status, target: str | Status) -> bool:
    return Status(target) in ALLOWED_TRANSITIONS[Status(current)]


def assert_transition(current: str | Status, target: str | Status) -> None:
    cur, tgt = Status(current), Status(target)
    if tgt not in ALLOWED_TRANSITIONS[cur]:
        raise InvalidTransition(cur, tgt)
