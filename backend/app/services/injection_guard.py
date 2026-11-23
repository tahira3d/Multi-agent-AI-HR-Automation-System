"""Heuristic prompt-injection scanner for candidate-supplied text.

Resume text is *untrusted data* (brief section 13/14). This scanner does not decide
anything by itself. It raises a warning for the HR reviewer and gives later agents a signal.
The real defence is architectural: candidate text is only ever passed to the LLM as quoted
data, and scores are computed by deterministic Python, never by instructions in a resume.
"""
from __future__ import annotations

import re

_PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|your)\b[^.\n]{0,30}\b(instructions?|rules?|prompts?|guidelines?)\b",
        re.I,
    ),
    "role_hijack": re.compile(
        r"\b(you are now|act as|pretend (to be|you are)|new instructions?)\b", re.I
    ),
    "system_prompt_reference": re.compile(r"\b(system|developer) (prompt|message|instructions?)\b", re.I),
    "score_manipulation": re.compile(
        r"\b(give|assign|award|rate|score)\b[^.\n]{0,40}\b(this )?(candidate|resume|applicant|me)\b[^.\n]{0,40}\b(100|10/10|maximum|highest|perfect|top)\b",
        re.I,
    ),
    "forced_recommendation": re.compile(
        r"\b(recommend|shortlist|hire|select)\b[^.\n]{0,30}\b(this|the) (candidate|applicant)\b[^.\n]{0,30}\b(immediately|without|always|at once)\b",
        re.I,
    ),
    "chat_markup": re.compile(r"(<\s*/?\s*(system|assistant|instructions?)\s*>|\[\s*/?INST\s*\]|<\|im_(start|end)\|>)", re.I),
    "data_exfiltration": re.compile(
        r"\b(reveal|print|output|show|send)\b[^.\n]{0,30}\b(api key|password|secret|other candidates|system prompt)\b", re.I
    ),
}


def scan_for_injection(text: str) -> list[str]:
    """Return the ids of the suspicious patterns found (empty list = nothing flagged)."""
    if not text:
        return []
    return [name for name, pattern in _PATTERNS.items() if pattern.search(text)]
