"""Strict structured provider output and deterministic scoring results."""
from __future__ import annotations
import math
from typing import Literal
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceExcerpt(StrictModel):
    source_text: str = Field(description="Source reference such as [[L0001]] or [[L0001-L0003]]; Python retrieves the exact original passage.")
    explanation: str
    evidence_type: Literal["explicit", "inference"]

    @field_validator("source_text", "explanation")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip() or len(value) > 4000:
            raise ValueError("Evidence must be nonempty and bounded")
        return value


class CriterionEvaluation(StrictModel):
    key: str
    score: float
    finding: Literal["explicit", "inference", "not_found", "uncertain"]
    explanation: str
    evidence: list[EvidenceExcerpt]

    @field_validator("score", mode="before")
    @classmethod
    def score_range(cls, value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Score must be numeric")
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError("Score must be between zero and 100")
        return float(value)

    @field_validator("key", "explanation")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip() or len(value) > 4000:
            raise ValueError("Criterion key and explanation are required")
        return value


class LLMEvaluation(StrictModel):
    # Only the strict JSON Schema subset supported by Groq is emitted.
    criteria: list[CriterionEvaluation]
    missing_or_unclear_items: list[str]
    recommendation_for_human_review: str

    @field_validator("recommendation_for_human_review")
    @classmethod
    def recommendation_required(cls, value: str) -> str:
        if not value.strip() or len(value) > 4000:
            raise ValueError("A bounded human-review recommendation is required")
        return value

    @field_validator("missing_or_unclear_items")
    @classmethod
    def bounded_items(cls, values: list[str]) -> list[str]:
        if len(values) > 100 or any(not v.strip() or len(v) > 2000 for v in values):
            raise ValueError("Invalid missing or unclear items")
        return values


class SourceLineEvidence(StrictModel):
    source_line_start: int = Field(description="First server-generated source line number; for [L0015] return 15.")
    source_line_end: int = Field(description="Last source line number, inclusive. Use the same number for one line.")
    explanation: str
    evidence_type: Literal["explicit", "inference"]

    @field_validator("source_line_start", "source_line_end", mode="before")
    @classmethod
    def positive_line_number(cls, value):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 999999:
            raise ValueError("Evidence requires a positive integer source line")
        return value


class SourceReferencedCriterion(CriterionEvaluation):
    evidence: list[SourceLineEvidence]


class SourceReferencedEvaluation(LLMEvaluation):
    """Provider wire schema uses integers so models cannot rewrite source quotations."""
    criteria: list[SourceReferencedCriterion]

    def to_evaluation(self) -> LLMEvaluation:
        value = self.model_dump()
        for criterion in value["criteria"]:
            for excerpt in criterion["evidence"]:
                first, last = excerpt.pop("source_line_start"), excerpt.pop("source_line_end")
                excerpt["source_text"] = f"[[L{first:04d}-L{last:04d}]]"
        return LLMEvaluation.model_validate(value)


class ResumeEvidence(StrictModel):
    criterion: str
    source_text: str
    explanation: str
    evidence_type: Literal["explicit", "inference"]
    source_start: int
    source_end: int


class ScoringResult(StrictModel):
    overall_score: float
    criterion_scores: dict[str, float]
    evidence: list[ResumeEvidence]
    missing_or_unclear_items: list[str]
    parsing_warnings: list[str]
    recommendation_for_human_review: str
    model_version: str
    criterion_findings: dict[str, str]
    criterion_explanations: dict[str, str]


class EvaluationOut(BaseModel):
    evaluation_id: UUID
    application_id: UUID
    overall_score: float
    total_score: float
    criterion_scores: dict[str, float]
    evidence: list[ResumeEvidence]
    criterion_findings: dict[str, str]
    criterion_explanations: dict[str, str]
    missing_or_unclear_items: list[str]
    parsing_warnings: list[str]
    recommendation_for_human_review: str
    model_version: str
    rubric_version: int
    rubric_snapshot: dict
    version: int
    is_active: bool
    created_at: datetime


class EvaluationRunOut(BaseModel):
    application_id: UUID
    status: str
    workflow_thread_id: str | None
    evaluation: EvaluationOut


class RankedEvaluationOut(EvaluationOut):
    candidate_id: UUID
    candidate_name: str
    current_status: str
    rubric_outdated: bool


class RankingsOut(BaseModel):
    items: list[RankedEvaluationOut]
    total: int
    offset: int
    limit: int


class ApprovalDecisionOut(BaseModel):
    approval_id: UUID
    application_id: UUID
    evaluation_id: UUID
    decision: Literal["approve", "decline", "hold"]
    reason: str | None
    reviewed_by: UUID
    reviewed_at: datetime
    status: str
