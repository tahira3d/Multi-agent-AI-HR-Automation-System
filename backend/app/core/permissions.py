"""Role -> permission map (least privilege). Roles follow section 5 of the brief."""
from __future__ import annotations

from enum import Enum


class Role(str, Enum):
    RECRUITER = "recruiter"
    INTERVIEWER = "interviewer"  # hiring manager / interviewer
    ADMIN = "admin"


class Permission(str, Enum):
    MANAGE_REQUISITIONS = "manage_requisitions"
    UPLOAD_RESUMES = "upload_resumes"
    VIEW_CANDIDATES = "view_candidates"
    APPROVE_SHORTLIST = "approve_shortlist"
    APPROVE_SCREENING = "approve_screening"
    APPROVE_INVITATION = "approve_invitation"
    VIEW_AUDIT = "view_audit"
    MANAGE_USERS = "manage_users"
    EVALUATE_RESUMES = "evaluate_resumes"
    DELETE_CANDIDATE_DATA = "delete_candidate_data"
    VIEW_FAIRNESS = "view_fairness"


_ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.RECRUITER: frozenset(
        {
            Permission.MANAGE_REQUISITIONS,
            Permission.UPLOAD_RESUMES,
            Permission.VIEW_CANDIDATES,
            Permission.APPROVE_SHORTLIST,
            Permission.APPROVE_SCREENING,
            Permission.APPROVE_INVITATION,
            Permission.VIEW_AUDIT,
            Permission.EVALUATE_RESUMES,
            Permission.DELETE_CANDIDATE_DATA,
        }
    ),
    # Interviewers only see candidates who have reached the interview stage (enforced in API later).
    Role.INTERVIEWER: frozenset({Permission.VIEW_CANDIDATES}),
    Role.ADMIN: frozenset({Permission.MANAGE_USERS, Permission.VIEW_AUDIT,
                           Permission.VIEW_CANDIDATES, Permission.DELETE_CANDIDATE_DATA, Permission.VIEW_FAIRNESS}),
}


def has_permission(role: str, permission: Permission) -> bool:
    try:
        return permission in _ROLE_PERMISSIONS[Role(role)]
    except ValueError:
        return False
