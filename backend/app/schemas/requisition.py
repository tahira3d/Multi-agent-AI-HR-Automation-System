"""Requisition + scoring-rubric schemas (FR-01, AC-01). Pydantic only (no web framework)."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import re
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class RubricCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=2, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=2, max_length=80)
    weight: float = Field(gt=0, le=100)
    justification: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def job_related_only(self):
        value = f"{self.key} {self.label}".replace("_", " ")
        if re.search(r"\b(age|gender|sex|religion|race|ethnicity|nationality|marital|disability|health|accent|personality)\b", value, re.I):
            raise ValueError("Rubric criteria must be job-related and cannot assess protected or personal characteristics")
        return self


DEFAULT_RUBRIC = [
    RubricCriterion(key="required_skills", label="Required skills", weight=40,
                    justification="Must-haves drive day-one productivity."),
    RubricCriterion(key="relevant_experience", label="Relevant experience", weight=25,
                    justification="Depth of related professional work."),
    RubricCriterion(key="role_projects", label="Role-related projects", weight=20,
                    justification="Evidence of applied skill beyond job titles."),
    RubricCriterion(key="education_certifications", label="Education / certifications", weight=10,
                    justification="Only where relevant to the role."),
    RubricCriterion(key="other_job_related", label="Other job-related evidence", weight=5,
                    justification="Explicitly job-related extras."),
]


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    criteria: list[RubricCriterion] = Field(min_length=1, max_length=12)

    @field_validator("criteria")
    @classmethod
    def unique_keys(cls, v: list[RubricCriterion]):
        keys = [c.key for c in v]
        if len(keys) != len(set(keys)):
            raise ValueError("criterion keys must be unique")
        return v

    @model_validator(mode="after")
    def weights_sum_to_100(self):
        total = sum(c.weight for c in self.criteria)
        if abs(total - 100.0) > 0.000001:
            raise ValueError(f"rubric weights must sum to 100 (got {total:g})")
        return self


class RequisitionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_title: str = Field(min_length=3, max_length=120)
    job_description: str = Field(min_length=30, max_length=20000)
    required_skills: list[str] = Field(default_factory=list, max_length=40)
    preferred_skills: list[str] = Field(default_factory=list, max_length=40)
    min_experience_years: float | None = Field(default=None, ge=0, le=50, validation_alias=AliasChoices("min_experience_years", "minimum_experience"))
    location: str | None = Field(default=None, max_length=200)
    work_arrangement: str | None = Field(default=None, max_length=120)
    screening_questions: list[str] = Field(default_factory=list, max_length=15)
    rubric: Rubric = Field(default_factory=lambda: Rubric(criteria=DEFAULT_RUBRIC), validation_alias=AliasChoices("rubric", "evaluation_rubric"))

    @field_validator("required_skills", "preferred_skills", "screening_questions")
    @classmethod
    def bounded_items(cls, values):
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("Each item must be nonempty and at most 500 characters")
        return list(dict.fromkeys(value.strip() for value in values))


class RequisitionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_title: str | None = Field(default=None, min_length=3, max_length=120)
    job_description: str | None = Field(default=None, min_length=30, max_length=20000)
    required_skills: list[str] | None = None
    preferred_skills: list[str] | None = None
    min_experience_years: float | None = Field(default=None, ge=0, le=50)
    location: str | None = Field(default=None, max_length=200)
    work_arrangement: str | None = Field(default=None, max_length=120)
    screening_questions: list[str] | None = None
    rubric: Rubric | None = None
    expected_rubric_version: int | None = Field(default=None, ge=1)


class RequisitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    requisition_id: UUID
    created_by: UUID
    job_title: str
    job_description: str
    required_skills: list[str]
    preferred_skills: list[str]
    min_experience_years: float | None
    work_arrangement: str | None
    location: str | None
    screening_questions: list[str]
    rubric: dict
    rubric_version: int
    status: str
    created_at: datetime
    updated_at: datetime
