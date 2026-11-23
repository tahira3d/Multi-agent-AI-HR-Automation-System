"""Best-effort extraction of candidate name / email / phone from resume text.

Deliberately simple and conservative: when unsure it returns None and HR can edit the
record. Never guesses a name from a skills line or a heading.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePath

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d\s\-().]{8,17}\d(?!\w)")
_HEADINGS = {
    "resume", "curriculum vitae", "cv", "summary", "skills", "experience", "education",
    "profile", "objective", "projects", "contact",
}


@dataclass
class Contact:
    name: str | None
    email: str | None
    phone: str | None


def _looks_like_name(line: str) -> bool:
    line = line.strip()
    if not line or line.lower() in _HEADINGS or any(ch.isdigit() for ch in line) or "@" in line:
        return False
    words = line.split()
    return 2 <= len(words) <= 4 and all(re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", w) for w in words)


def extract_contact(text: str) -> Contact:
    email = _EMAIL.search(text)
    phone = _PHONE.search(text)
    name = None
    for line in [item for item in text.splitlines() if item.strip()][:5]:
        if _looks_like_name(line):
            name = " ".join(w.capitalize() if w.isupper() else w for w in line.split())
            break
    return Contact(name=name, email=email.group(0).lower() if email else None,
                   phone=re.sub(r"\s+", " ", phone.group(0)).strip() if phone else None)


def name_from_filename(filename: str) -> str:
    stem = PurePath(filename).stem
    stem = re.sub(r"^\d+[_\-\s]*", "", stem)
    return re.sub(r"[_\-]+", " ", stem).strip().title() or "Unknown Candidate"
