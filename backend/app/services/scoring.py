"""Provider-independent rubric, provenance and deterministic score validation."""
from __future__ import annotations
import math
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from app.integrations.groq_cloud import InvalidEvaluationError
from app.schemas.evaluation import LLMEvaluation, ScoringResult


_SOURCE_REFERENCE = re.compile(r"\[\[L([0-9]{1,6})(?:-L([0-9]{1,6}))?\]\]")
_LINE_ENDINGS = "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def build_source_spans(text: str) -> list[tuple[int, int]]:
    """Visible numbered source lines, with immutable offsets into the input.

    Long physical paragraphs are split into bounded logical lines. The source
    is never rewritten, and whitespace between a range's lines is retained.
    """
    spans = []
    offset = 0
    for physical_line in text.splitlines(keepends=True):
        end = offset + len(physical_line.rstrip(_LINE_ENDINGS))
        start = offset
        while start < end:
            limit = min(start + 800, end)
            if limit < end:
                breaks = list(re.finditer(r"\s+", text[start:limit]))
                if breaks and breaks[-1].start() >= 400:
                    limit = start + breaks[-1].end()
            left, right = start, limit
            while left < right and text[left].isspace():
                left += 1
            while right > left and text[right - 1].isspace():
                right -= 1
            if left < right:
                spans.append((left, right))
            start = limit
        offset += len(physical_line)
    return spans


def number_resume_lines(text: str, spans: list[tuple[int, int]] | None = None) -> str:
    spans = build_source_spans(text) if spans is None else spans
    return "\n".join(f"[L{index:04d}] {text[start:end]}" for index, (start, end) in enumerate(spans, 1))


def resolve_evidence_span(source: str, text: str,
                          spans: list[tuple[int, int]] | None = None) -> tuple[int, int]:
    """Resolve a server-generated line reference or an exact legacy quotation.

    Legacy matching tolerates layout whitespace only. It never changes words,
    punctuation, or case, and always returns an exact substring of the source.
    """
    reference = _SOURCE_REFERENCE.fullmatch(source.strip())
    if reference:
        spans = build_source_spans(text) if spans is None else spans
        first = int(reference.group(1))
        last = int(reference.group(2) or first)
        if not 1 <= first <= last <= len(spans) or last - first + 1 > 12:
            raise InvalidEvaluationError("An evidence source-line reference is outside the allowed range.")
        start, end = spans[first - 1][0], spans[last - 1][1]
        if end - start > 4000 or not text[start:end].strip():
            raise InvalidEvaluationError("An evidence source passage is empty or too long.")
        return start, end
    if re.search(r"\[{1,2}\s*L", source):
        raise InvalidEvaluationError("Evidence must contain one valid source-line reference, without added prose.")
    if len(source) > 4000 or not source.strip():
        raise InvalidEvaluationError("An evidence source passage is empty or too long.")
    start = text.find(source)
    if source.strip() and start >= 0:
        return start, start + len(source)
    parts = source.split()
    if parts:
        pattern = r"\s+".join(re.escape(part) for part in parts)
        match = re.search(pattern, text)
        if match and match.end() - match.start() <= 4000:
            return match.start(), match.end()
    raise InvalidEvaluationError("A cited source passage is absent from the resume.")


def normalized_criteria(rubric: dict[str, Any]) -> list[dict[str, Any]]:
    criteria = rubric.get("criteria") if isinstance(rubric, dict) else None
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 12:
        raise InvalidEvaluationError("The approved rubric must contain 1 to 12 criteria.")
    result, keys = [], set()
    total = Decimal("0")
    try:
        for item in criteria:
            key = item["key"]
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", key) or key in keys:
                raise InvalidEvaluationError("Rubric criterion keys must be unique stable identifiers.")
            if isinstance(item["weight"], bool):
                raise InvalidEvaluationError("Rubric weights must be numeric.")
            weight = Decimal(str(item["weight"]))
            if not weight.is_finite() or not 0 < weight <= 100:
                raise InvalidEvaluationError("Rubric weights must be greater than zero and at most 100.")
            keys.add(key)
            total += weight
            result.append({"key": key, "label": str(item.get("label", key)),
                           "weight": float(weight), "justification": str(item.get("justification", ""))})
    except (KeyError, TypeError, InvalidOperation):
        raise InvalidEvaluationError("The approved rubric is malformed.") from None
    if abs(total - Decimal("100")) > Decimal("0.000001"):
        raise InvalidEvaluationError("Rubric weights must total 100 percent.")
    return result


def calculate_weighted_score(scores: dict[str, float], rubric: dict[str, Any]) -> float:
    criteria = normalized_criteria(rubric)
    if set(scores) != {item["key"] for item in criteria}:
        raise InvalidEvaluationError("Evaluation criteria do not match the approved rubric.")
    total = Decimal("0")
    for item in criteria:
        score = scores[item["key"]]
        if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score) or not 0 <= score <= 100:
            raise InvalidEvaluationError("Criterion scores must be finite numbers between zero and 100.")
        total += Decimal(str(score)) * Decimal(str(item["weight"])) / Decimal("100")
    return float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_evidence(response: LLMEvaluation | ScoringResult, resume_text: str, rubric: dict[str, Any],
                      *, source_spans: list[tuple[int, int]] | None = None) -> None:
    expected = {item["key"] for item in normalized_criteria(rubric)}
    if isinstance(response, LLMEvaluation):
        keys = [item.key for item in response.criteria]
        if len(keys) != len(expected) or set(keys) != expected:
            raise InvalidEvaluationError("The provider must evaluate every approved criterion exactly once.")
        for criterion in response.criteria:
            if len(criterion.evidence) > 20:
                raise InvalidEvaluationError("Too many evidence passages were returned.")
            if criterion.finding == "not_found" and (criterion.score != 0 or criterion.evidence):
                raise InvalidEvaluationError("Not-found findings must not claim supporting evidence or a positive score.")
            if criterion.score > 0 and not criterion.evidence:
                raise InvalidEvaluationError("Positive scores require traceable source evidence.")
            if criterion.finding in {"explicit", "inference"} and not criterion.evidence:
                raise InvalidEvaluationError("Evidence findings require source passages.")
            if criterion.finding == "explicit" and not any(e.evidence_type == "explicit" for e in criterion.evidence):
                raise InvalidEvaluationError("Explicit findings require explicit evidence.")
            for excerpt in criterion.evidence:
                resolve_evidence_span(excerpt.source_text, resume_text, source_spans)
        if any(c.finding in {"not_found", "uncertain"} for c in response.criteria) and not response.missing_or_unclear_items:
            raise InvalidEvaluationError("Missing or uncertain evidence needs a human-readable explanation.")
        calculate_weighted_score({c.key: c.score for c in response.criteria}, rubric)
        return
    if set(response.criterion_scores) != expected:
        raise InvalidEvaluationError("Evaluation criteria do not match the approved rubric.")
    supported = set()
    for evidence in response.evidence:
        if evidence.criterion not in expected or not evidence.source_text.strip():
            raise InvalidEvaluationError("Evidence refers to an invalid criterion or empty source.")
        if evidence.source_start < 0 or evidence.source_end <= evidence.source_start or resume_text[evidence.source_start:evidence.source_end] != evidence.source_text:
            raise InvalidEvaluationError("Evidence offsets do not resolve to the cited resume passage.")
        supported.add(evidence.criterion)
    if any(score > 0 and key not in supported for key, score in response.criterion_scores.items()):
        raise InvalidEvaluationError("Positive scores require traceable source evidence.")
    if abs(calculate_weighted_score(response.criterion_scores, rubric) - response.overall_score) > 0.001:
        raise InvalidEvaluationError("The overall score must equal the Python weighted calculation.")
