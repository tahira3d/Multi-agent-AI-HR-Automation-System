"""Versioned evaluations and durable, strictly human-authorized progression."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException
from langgraph.types import Command
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import accessible_application, accessible_requisition
from app.core.config import get_settings
from app.core.permissions import Permission, has_permission
from app.core.status import Status, assert_transition
from app.database.models import Application, Approval, Candidate, Evaluation, Requisition, ResumeFile
from app.services.audit_service import record_event
from app.workflows.hiring_graph import build_hiring_graph, persistent_checkpointer, workflow_thread_id


class EvaluationFailure(Exception):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.message, self.status_code = message, status_code


def evaluation_dict(value: Evaluation) -> dict[str, Any]:
    return {
        "evaluation_id": str(value.evaluation_id), "application_id": str(value.application_id),
        "overall_score": value.total_score, "total_score": value.total_score,
        "criterion_scores": value.criterion_scores, "evidence": value.evidence,
        "criterion_findings": value.criterion_findings or {},
        "criterion_explanations": value.criterion_explanations or {},
        "missing_or_unclear_items": value.missing_or_unclear, "parsing_warnings": value.warnings,
        "model_version": value.model_version, "rubric_version": value.rubric_version,
        "rubric_snapshot": value.rubric_snapshot, "version": value.version, "is_active": value.is_active,
        "recommendation_for_human_review": value.recommendation_for_human_review, "created_at": value.created_at,
    }


def approval_dict(value: Approval, status: str) -> dict[str, Any]:
    return {"approval_id": str(value.approval_id), "application_id": str(value.application_id),
            "evaluation_id": str(value.evaluation_id), "decision": value.decision, "reason": value.reason,
            "reviewed_by": str(value.reviewed_by), "reviewed_at": value.reviewed_at, "status": status}


class EvaluationService:
    def __init__(self, db: Session, user: Any, *, agent: Any = None,
                 checkpointer: Any = None, settings: Any = None):
        self.db, self.user, self.agent = db, user, agent
        self.checkpointer, self.settings = checkpointer, settings or get_settings()
        self.token: str | None = None

    def _application(self, application_id: UUID | str) -> Application:
        return accessible_application(self.db, UUID(str(application_id)), self.user)

    def _event(self, application_id: UUID | str, action: str, details: dict) -> None:
        record_event(self.db, action=action, actor_id=self.user.user_id, application_id=UUID(str(application_id)),
                     entity_type="application", entity_id=str(application_id), details=details)

    def _claim(self, app: Application, version: int, thread_id: str) -> None:
        previous_version, app_id = app.evaluation_version, app.application_id
        previous_status = app.current_status
        self.token = str(uuid4())
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        changed = self.db.execute(update(Application).where(
            Application.application_id == app_id, Application.evaluation_version == previous_version,
            Application.current_status == previous_status,
            or_(Application.processing_token.is_(None), Application.processing_started_at < cutoff),
        ).values(processing_token=self.token, processing_started_at=datetime.now(timezone.utc),
                 evaluation_version=version, workflow_thread_id=thread_id),
            execution_options={"synchronize_session": False}).rowcount
        if changed != 1:
            self.db.rollback()
            self.token = None
            raise HTTPException(409, "This application is already being processed; retry after it finishes.")
        self.db.commit()
        self.db.expire_all()

    def _release(self, application_id: UUID | str) -> None:
        if self.token:
            self.db.rollback()
            self.db.execute(update(Application).where(
                Application.application_id == UUID(str(application_id)), Application.processing_token == self.token,
            ).values(processing_token=None, processing_started_at=None),
                execution_options={"synchronize_session": False})
            self.db.commit()
            self.token = None
            self.db.expire_all()

    @contextmanager
    def _graph(self):
        if self.checkpointer is not None:
            yield build_hiring_graph(self, self.checkpointer)
        else:
            with persistent_checkpointer(self.settings) as saver:
                yield build_hiring_graph(self, saver)

    def _locked_requisition(self, app: Application) -> Requisition:
        # Refresh from the database; do not trust an identity-map copy after a slow LLM call.
        return self.db.scalar(select(Requisition).where(Requisition.requisition_id == app.requisition_id)
                              .with_for_update().execution_options(populate_existing=True))

    def latest_evaluation(self, application_id: UUID | str, version: int | None = None) -> Evaluation:
        app = self._application(application_id)
        query = select(Evaluation).where(Evaluation.application_id == app.application_id)
        query = query.where(Evaluation.version == version) if version is not None else query.where(Evaluation.is_active.is_(True))
        value = self.db.scalar(query.order_by(Evaluation.version.desc()))
        if value is None:
            raise HTTPException(404, "No evaluation exists for this application and version.")
        return value

    def evaluate(self, application_id: UUID | str, *, force_reevaluation: bool = False,
                 expected_rubric_version: int | None = None) -> dict:
        if not has_permission(self.user.role, Permission.EVALUATE_RESUMES):
            raise HTTPException(403, "Resume evaluation is not permitted for this role")
        app = self._application(application_id)
        req = accessible_requisition(self.db, app.requisition_id, self.user)
        rubric_version = req.rubric_version
        if req.status != "open":
            raise HTTPException(409, "Evaluation requires an open, approved requisition.")
        if expected_rubric_version is not None and expected_rubric_version != rubric_version:
            raise HTTPException(409, "The job rubric changed. Reload before evaluating.")
        resume = self.db.scalar(select(ResumeFile).where(ResumeFile.application_id == app.application_id)
                               .order_by(ResumeFile.uploaded_at.desc(), ResumeFile.file_id.desc()))
        if resume is None:
            raise HTTPException(422, "Upload a resume before starting evaluation.")
        current = self.db.scalar(select(Evaluation).where(
            Evaluation.application_id == app.application_id, Evaluation.is_active.is_(True)))
        reusable = bool(current and current.rubric_version == rubric_version and current.resume_file_id == resume.file_id)
        if reusable and not force_reevaluation:
            decision = self.db.scalar(select(Approval).where(
                Approval.evaluation_id == current.evaluation_id, Approval.approval_type == "shortlist"))
            if decision:
                return {"application_id": str(app.application_id), "status": app.current_status,
                        "workflow_thread_id": app.workflow_thread_id, "evaluation": evaluation_dict(current)}
        if app.current_status in {Status.APPROVED_FOR_SCREENING, Status.CLOSED}:
            if reusable and not force_reevaluation:
                return {"application_id": str(app.application_id), "status": app.current_status,
                        "workflow_thread_id": app.workflow_thread_id, "evaluation": evaluation_dict(current)}
            raise HTTPException(409, "A finalized shortlist decision cannot be reset by evaluation.")
        if reusable and not force_reevaluation:
            version = current.version
        elif not force_reevaluation and app.evaluation_version and (current is None or app.evaluation_version > current.version):
            version = app.evaluation_version
        else:
            version = app.evaluation_version + 1
        thread_id = workflow_thread_id(str(app.application_id), version)
        app_id, resume_id = app.application_id, resume.file_id
        self._claim(app, version, thread_id)
        state = {"application_id": str(app_id), "actor_id": str(self.user.user_id),
                 "resume_file_id": str(resume_id), "evaluation_version": version, "rubric_version": rubric_version,
                 "request_key": f"{app_id}:{version}", "error": "", "evaluation_id": "", "evaluation": {},
                 "missing_settings": []}
        try:
            with self._graph() as graph:
                config = {"configurable": {"thread_id": thread_id}}
                snapshot = graph.get_state(config)
                if reusable and not force_reevaluation and snapshot.values.get("evaluation_id") == str(current.evaluation_id) and snapshot.next == ("await_recruiter",):
                    result = snapshot.values
                elif (snapshot.next and not snapshot.values.get("error")
                      and snapshot.values.get("rubric_version") == rubric_version
                      and snapshot.values.get("resume_file_id") == str(resume_id)
                      and not force_reevaluation):
                    result = graph.invoke(None, config)
                else:
                    result = graph.invoke(state, config)
                if result.get("error"):
                    if result["error"] in {"groq_not_configured", "azure_not_configured"}:
                        if result["error"] == "azure_not_configured":
                            missing = result.get("missing_settings", [])
                            raise EvaluationFailure("Azure OpenAI is not configured. Missing settings: "
                                + ", ".join(missing) + ". Configure Azure and restart the backend, then retry.", 503)
                        from app.integrations.groq_cloud import GROQ_SETTING_NAMES
                        missing = [name for name in GROQ_SETTING_NAMES if name in result.get("missing_settings", [])]
                        raise EvaluationFailure("Legacy GroqCloud is not configured. Missing settings: "
                            + ", ".join(missing) + ". Configure Azure OpenAI or the development-only Groq provider, "
                            "then restart the backend.", 503)
                    if result["error"] == "provider_authentication":
                        raise EvaluationFailure("GroqCloud authentication failed. Check GROQ_API_KEY and model access, then restart the backend.", 503)
                    if result["error"] == "provider_rate_limited":
                        raise EvaluationFailure("GroqCloud rate limit exceeded. Retry later; no score was saved.", 503)
                    if result["error"] == "provider_unavailable":
                        raise EvaluationFailure("GroqCloud could not complete this request. Check provider availability, model access or quota, then retry.", 503)
                    message = ("Groq returned evidence that could not be verified against the extracted resume. "
                               "Retry evaluation or review the document manually; this is an evidence-validation failure.")
                    if result["error"] in {"resume_requires_manual_review", "resume_not_found", "insufficient_extractable_text"}:
                        message = "The resume does not have reliable extracted text. Upload a readable PDF or DOCX, or review it manually."
                    raise EvaluationFailure(message, 503 if result["error"] == "provider_unavailable" else 422)
            self.db.expire_all()
            value = self.latest_evaluation(app_id)
            return {"application_id": str(app_id), "status": self._application(app_id).current_status,
                    "workflow_thread_id": thread_id, "evaluation": evaluation_dict(value)}
        except (HTTPException, EvaluationFailure):
            raise
        except Exception as exc:
            self.db.rollback()
            raise EvaluationFailure("Workflow persistence is temporarily unavailable. Retry this application.") from exc
        finally:
            self._release(app_id)

    def decide(self, application_id: UUID | str, *, evaluation_id: UUID | str,
               decision: str, reason: str | None = "") -> dict:
        if not has_permission(self.user.role, Permission.APPROVE_SHORTLIST):
            raise HTTPException(403, "Only authorized HR recruiters may decide progression")
        if decision not in {"approve", "decline", "hold"}:
            raise HTTPException(422, "Decision must be approve, decline, or hold.")
        reason = (reason or "").strip()
        if len(reason) > 2000 or (decision in {"decline", "hold"} and not reason):
            raise HTTPException(422, "A bounded reason is required when declining or holding an application.")
        app = self._application(application_id)
        evaluation = self.latest_evaluation(app.application_id)
        if evaluation.evaluation_id != UUID(str(evaluation_id)):
            raise HTTPException(409, "Review the current active evaluation before deciding.")
        previous = self.db.scalar(select(Approval).where(
            Approval.evaluation_id == evaluation.evaluation_id, Approval.approval_type == "shortlist"))
        if previous:
            if previous.decision != decision:
                raise HTTPException(409, "This evaluation already has a conflicting recruiter decision.")
            return approval_dict(previous, app.current_status)
        req = accessible_requisition(self.db, app.requisition_id, self.user)
        if req.rubric_version != evaluation.rubric_version:
            raise HTTPException(409, "This evaluation uses an outdated rubric. Reevaluate before deciding.")
        if app.current_status != Status.SCORED_HR_REVIEW:
            raise HTTPException(409, "Only a scored application awaiting HR review may receive this decision.")
        thread_id = workflow_thread_id(str(app.application_id), evaluation.version)
        app_id, eval_id, resume_id = app.application_id, evaluation.evaluation_id, evaluation.resume_file_id
        initial = {"application_id": str(app_id), "actor_id": str(self.user.user_id),
                   "resume_file_id": str(resume_id), "evaluation_version": evaluation.version,
                   "rubric_version": evaluation.rubric_version, "request_key": evaluation.request_key,
                   "error": "", "evaluation_id": "", "evaluation": {}}
        self._claim(app, app.evaluation_version, thread_id)
        try:
            with self._graph() as graph:
                config = {"configurable": {"thread_id": thread_id}}
                snapshot = graph.get_state(config)
                if snapshot.next and snapshot.next != ("await_recruiter",):
                    graph.invoke(None, config)
                    snapshot = graph.get_state(config)
                if not snapshot.values or snapshot.next != ("await_recruiter",):
                    graph.invoke(initial, config)
                graph.invoke(Command(resume={"evaluation_id": str(eval_id), "reviewer_id": str(self.user.user_id),
                                             "decision": decision, "reason": reason}), config)
            self.db.expire_all()
            approval = self.db.scalar(select(Approval).where(
                Approval.evaluation_id == eval_id, Approval.approval_type == "shortlist"))
            if approval is None:
                raise EvaluationFailure("The approval checkpoint is unavailable. Retry the decision.")
            return approval_dict(approval, self._application(app_id).current_status)
        except (HTTPException, EvaluationFailure):
            raise
        except Exception as exc:
            self.db.rollback()
            raise EvaluationFailure("Approval could not be resumed. The saved review can be retried.") from exc
        finally:
            self._release(app_id)

    def rankings(self, requisition_id: UUID | str, *, offset: int = 0, limit: int = 50, status: str | None = None) -> dict:
        req = accessible_requisition(self.db, UUID(str(requisition_id)), self.user)
        conditions = [Application.requisition_id == req.requisition_id, Evaluation.is_active.is_(True)]
        if status:
            conditions.append(Application.current_status == status)
        query = select(Application, Candidate, Evaluation).join(
            Candidate, Candidate.candidate_id == Application.candidate_id).join(
            Evaluation, Evaluation.application_id == Application.application_id).where(*conditions)
        total = self.db.scalar(select(func.count()).select_from(query.subquery())) or 0
        rows = self.db.execute(query.order_by(Evaluation.total_score.desc(), Application.created_at)
                               .offset(offset).limit(limit)).all()
        return {"items": [{**evaluation_dict(value), "candidate_id": str(candidate.candidate_id),
                           "candidate_name": candidate.full_name, "current_status": app.current_status,
                           "rubric_outdated": value.rubric_version != req.rubric_version}
                          for app, candidate, value in rows], "total": total, "offset": offset, "limit": limit}

    def validate_application(self, state: dict) -> dict:
        app = self._application(state["application_id"])
        req = accessible_requisition(self.db, app.requisition_id, self.user)
        if app.processing_token != self.token:
            raise HTTPException(409, "Processing ownership changed; retry the application.")
        if req.status != "open" or req.rubric_version != state["rubric_version"]:
            raise HTTPException(409, "The requisition or rubric changed. Reload before evaluating.")
        existing = self.db.scalar(select(Evaluation).where(Evaluation.request_key == state["request_key"]))
        return {"evaluation_id": str(existing.evaluation_id) if existing else "", "error": ""}

    def parse_resume(self, state: dict) -> dict:
        if state.get("evaluation_id"):
            return {}
        resume = self.db.get(ResumeFile, UUID(state["resume_file_id"]))
        if resume is None or str(resume.application_id) != state["application_id"]:
            return {"error": "resume_not_found"}
        return {} if resume.parse_status == "parsed" else {"error": "resume_requires_manual_review"}

    def validate_extracted_content(self, state: dict) -> dict:
        if state.get("evaluation_id"):
            return {}
        resume = self.db.get(ResumeFile, UUID(state["resume_file_id"]))
        return {} if resume and resume.extracted_text and len(resume.extracted_text.strip()) >= 30 else {"error": "insufficient_extractable_text"}

    def _scoring_inputs(self, state: dict):
        from app.services.redaction import redact_resume_text
        app = self._application(state["application_id"])
        req = accessible_requisition(self.db, app.requisition_id, self.user)
        resume = self.db.get(ResumeFile, UUID(state["resume_file_id"]))
        text = redact_resume_text(resume.extracted_text or "", known_names=[app.candidate.full_name]).text
        job = {"job_title": req.job_title, "job_description": req.job_description, "required_skills": req.required_skills,
               "preferred_skills": req.preferred_skills, "minimum_experience": req.min_experience_years}
        return req, resume, text, job

    def extract_evidence(self, state: dict) -> dict:
        if state.get("evaluation_id"):
            return {}
        from app.agents.resume_agent import get_resume_agent
        from app.integrations.azure_provider import AzureConfigurationError
        from app.integrations.groq_cloud import (
            GroqConfigurationError, ProviderAuthenticationError, ProviderRateLimitError,
            ProviderUnavailableError, ScoringError,
        )
        req, resume, text, job = self._scoring_inputs(state)
        try:
            result = (self.agent or get_resume_agent()).evaluate(
                resume_text=text, job=job, rubric=req.rubric, parsing_warnings=list(resume.warnings or []))
            return {"evaluation": result.model_dump(mode="json")}
        except AzureConfigurationError as exc:
            return {"error": "azure_not_configured", "missing_settings": list(exc.missing_settings)}
        except GroqConfigurationError as exc:
            return {"error": "groq_not_configured", "missing_settings": list(exc.missing_settings)}
        except ProviderAuthenticationError:
            return {"error": "provider_authentication"}
        except ProviderRateLimitError:
            return {"error": "provider_rate_limited"}
        except ProviderUnavailableError:
            return {"error": "provider_unavailable"}
        except ScoringError:
            return {"error": "invalid_or_unavailable_evidence"}

    def evaluate_rubric(self, state: dict) -> dict:
        if state.get("evaluation_id"):
            return {}
        req, _, text, _ = self._scoring_inputs(state)
        result = state["evaluation"]
        expected = {c["key"] for c in req.rubric["criteria"]}
        if set(result.get("criterion_scores", {})) != expected:
            return {"error": "criterion_mismatch"}
        supported = set()
        for item in result.get("evidence", []):
            quote = item.get("source_text", "")
            start, end = item.get("source_start"), item.get("source_end")
            if item.get("criterion") not in expected or not quote or quote not in text:
                return {"error": "untraceable_evidence"}
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or text[start:end] != quote:
                return {"error": "invalid_evidence_offsets"}
            supported.add(item["criterion"])
        if any(score > 0 and key not in supported for key, score in result["criterion_scores"].items()):
            return {"error": "unsupported_positive_score"}
        return {}

    def calculate_score(self, state: dict) -> dict:
        if state.get("evaluation_id"):
            return {}
        from app.services.scoring import calculate_weighted_score
        req, _, _, _ = self._scoring_inputs(state)
        result = dict(state["evaluation"])
        result["overall_score"] = calculate_weighted_score(result["criterion_scores"], req.rubric)
        return {"evaluation": result}

    def persist_evaluation(self, state: dict) -> dict:
        app = self._application(state["application_id"])
        req = self._locked_requisition(app)
        if req.status != "open" or req.rubric_version != state["rubric_version"]:
            raise HTTPException(409, "The rubric changed during evaluation. Reevaluate with the new rubric.")
        existing = self.db.scalar(select(Evaluation).where(Evaluation.request_key == state["request_key"]))
        if existing:
            return {"evaluation_id": str(existing.evaluation_id), "status": app.current_status, "evaluation": {}}
        assert_transition(app.current_status, Status.SCORED_HR_REVIEW)
        changed = self.db.execute(update(Application).where(
            Application.application_id == app.application_id, Application.processing_token == self.token,
            Application.evaluation_version == state["evaluation_version"],
        ).values(current_status=Status.SCORED_HR_REVIEW), execution_options={"synchronize_session": False}).rowcount
        if changed != 1:
            self.db.rollback()
            raise HTTPException(409, "Processing ownership changed; retry the application.")
        result = state["evaluation"]
        self.db.execute(update(Evaluation).where(Evaluation.application_id == app.application_id,
                                                Evaluation.is_active.is_(True)).values(is_active=False))
        value = Evaluation(application_id=app.application_id, resume_file_id=UUID(state["resume_file_id"]),
            model_version=result["model_version"], rubric_version=state["rubric_version"],
            version=state["evaluation_version"], is_active=True, request_key=state["request_key"],
            rubric_snapshot=req.rubric, total_score=result["overall_score"],
            criterion_scores=result["criterion_scores"], evidence=result["evidence"],
            criterion_findings=result.get("criterion_findings", {}), criterion_explanations=result.get("criterion_explanations", {}),
            missing_or_unclear=result["missing_or_unclear_items"], warnings=result["parsing_warnings"],
            recommendation_for_human_review=result["recommendation_for_human_review"])
        self.db.add(value)
        self.db.flush()
        self._event(app.application_id, "evaluation.completed", {"evaluation_id": str(value.evaluation_id),
                    "version": value.version, "rubric_version": value.rubric_version, "model_version": value.model_version})
        self.db.commit()
        return {"evaluation_id": str(value.evaluation_id), "status": Status.SCORED_HR_REVIEW, "evaluation": {}}

    def mark_manual_review(self, state: dict) -> dict:
        app = self._application(state["application_id"])
        changed = self.db.execute(update(Application).where(
            Application.application_id == app.application_id, Application.processing_token == self.token,
            Application.evaluation_version == state["evaluation_version"],
        ).values(current_status=Status.NEEDS_MANUAL_REVIEW), execution_options={"synchronize_session": False}).rowcount
        if changed != 1:
            raise HTTPException(409, "Processing ownership changed; retry the application.")
        self._event(app.application_id, "evaluation.needs_manual_review",
                    {"failure_code": state.get("error", "evaluation_failed"), "evaluation_version": state["evaluation_version"]})
        self.db.commit()
        return {"status": Status.NEEDS_MANUAL_REVIEW, "evaluation": {}}

    def record_decision(self, state: dict) -> dict:
        payload = state["decision"]
        if not isinstance(payload, dict) or payload.get("reviewer_id") != str(self.user.user_id):
            raise HTTPException(403, "The decision must belong to the authenticated reviewer.")
        app = self._application(state["application_id"])
        evaluation = self.latest_evaluation(app.application_id)
        req = self._locked_requisition(app)
        if str(evaluation.evaluation_id) != payload.get("evaluation_id"):
            raise HTTPException(409, "The decision references a superseded evaluation.")
        if req.status != "open" or req.rubric_version != evaluation.rubric_version:
            raise HTTPException(409, "Reevaluate using the current rubric before deciding.")
        decision, reason = payload.get("decision"), str(payload.get("reason", "")).strip()
        targets = {"approve": Status.APPROVED_FOR_SCREENING, "decline": Status.CLOSED, "hold": Status.NEEDS_MANUAL_REVIEW}
        if decision not in targets or len(reason) > 2000 or (decision != "approve" and not reason):
            raise HTTPException(422, "Invalid decision or missing reviewer reason.")
        previous = self.db.scalar(select(Approval).where(
            Approval.evaluation_id == evaluation.evaluation_id, Approval.approval_type == "shortlist"))
        if previous:
            if previous.decision != decision:
                raise HTTPException(409, "A conflicting decision was already recorded.")
            return {"status": app.current_status}
        assert_transition(app.current_status, targets[decision])
        changed = self.db.execute(update(Application).where(
            Application.application_id == app.application_id, Application.current_status == Status.SCORED_HR_REVIEW,
            Application.evaluation_version == evaluation.version, Application.processing_token == self.token,
        ).values(current_status=targets[decision]), execution_options={"synchronize_session": False}).rowcount
        if changed != 1:
            self.db.rollback()
            raise HTTPException(409, "The application changed or was reviewed. Refresh the review.")
        approval = Approval(application_id=app.application_id, evaluation_id=evaluation.evaluation_id,
            approval_type="shortlist", reviewed_by=self.user.user_id, decision=decision,
            reason=reason, reviewed_at=datetime.now(timezone.utc))
        self.db.add(approval)
        self.db.flush()
        self._event(app.application_id, "shortlist.human_decision", {"evaluation_id": str(evaluation.evaluation_id),
                    "approval_id": str(approval.approval_id), "decision": decision, "new_status": targets[decision]})
        self.db.commit()
        return {"status": targets[decision]}

    def update_status(self, state: dict) -> dict:
        self.db.expire_all()
        return {"status": self._application(state["application_id"]).current_status}
