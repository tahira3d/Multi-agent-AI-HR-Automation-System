# Baseline audit — 2026-11-22

This is a brownfield upgrade of the existing application. The baseline uses Python
3.11.17, SQLite locally, FastAPI, Streamlit, SQLAlchemy 2.x, Alembic, LangGraph,
GroqCloud for Agent 1, and local Ollama/Faster-Whisper/Piper components for Agent 2.
No database engine, interpreter, provider credential, dependency, or service was
changed while preparing this audit.

## Commands and observed results

| Check | Command | Result |
| --- | --- | --- |
| Migration state | `python -m alembic -c backend/alembic.ini current` | `0004_auth_lockout_refresh (head)` on SQLite |
| Migration apply | `python -m alembic -c backend/alembic.ini upgrade head` | Succeeded; no pending revision was applied |
| Lint | `python -m ruff check backend frontend tests` | Passed |
| API health | `GET /health` and `GET /ready` | Both returned `status: ok/ready`; Groq configured, Azure not configured |
| Test suite | `python -m pytest -q` | **250 passed, 2 skipped in 50.07s** |

The first lint run found one unused import in the just-added Agent 3 assessment
module. It was removed; the subsequent lint run passed.

## Inventory

| Area | Existing implementation |
| --- | --- |
| API | `backend/app/api/` has authentication, requisitions, uploads, applications, candidates, Agent 1 evaluations, Agent 2 screenings, and an initial Agent 3 assessment endpoint. |
| Data | `models.py`, `screening_models.py`, and Alembic revisions `0001`–`0004`; local engine is SQLite, PostgreSQL remains supported. |
| Services | Versioned resume evaluation, audit hash chain, retention, resume parsing, scoring, screening, and initial final recommendation assembly. |
| Agents / workflows | Groq-backed resume scoring agent; hiring and screening LangGraph graphs with persistent SQLite/PostgreSQL checkpointer support. |
| UI | Login, dashboard, jobs, uploads, rankings, candidate review, screening HR review, and candidate voice-screening page. |
| Security | JWT/RBAC, Argon2, refresh tokens, account lockout, upload controls, evidence validation, injection checks, retention, and audit verification. |

## Gap table

| Requirement group | Status | Existing coverage | Planned additive change |
| --- | --- | --- | --- |
| Provider abstraction | Partial | Groq provider and dormant Azure settings exist. | Add `LLMProvider` protocol, Groq adapter compatibility wrapper, mocked Azure adapter, provider health/provenance. |
| Provider structured-output recovery | Partial | Agent 1 validates Pydantic output and handles provider failures. | Bound malformed-output repair to two attempts and persist provider/model/prompt hashes. |
| Statuses and four gates | Partial | Resume shortlist and screening review exist; no `On Hold` or `Interview Scored — HR Review`. | Add reversible migration, transition compatibility map, audited illegal transitions, gates 2–4. |
| Rubrics | Partial | Versioned resume rubrics exist. | Add immutable interview rubric versioning and anchors. |
| Agent 1 v2 | Partial | Evidence-backed Groq scoring is active. | Add flag-gated staged pipeline, verifier, lexical signals, abstention and v1 compatibility. |
| Agent 2 mock call model | Partial | Consent-aware local voice screening and bounded follow-ups exist. | Add explicit simulated-call modes, call states, compliance gate, redaction and replay/persona paths without replacing voice flow. |
| Agent 3 scoring and reports | Missing | Initial read-only combined assessment endpoint exists. | Add transcript-evidence scoring, deterministic score calculation, report persistence and PDF/DOCX/JSON downloads. |
| Fairness runtime | Missing | Protected-topic filtering and redaction exist. | Add counterfactual and cohort findings subsystem; scoring code will not read demographic data. |
| Provenance | Partial | Hash-chained audit exists. | Add LLM provenance hashes/latency/retry records and report-download events. |
| Operations | Partial | Settings, Docker Compose, health/ready and local scripts exist. | Add live health alias, metrics, CI/hardening documentation, keeping the current services and small footprint. |
| Synthetic/evaluation harness | Partial | Existing synthetic tests and sample data exist. | Add small deterministic transcripts, counterfactual cases and evaluation scripts; no external datasets. |

## Dependency decision

No dependency is proposed in M0. Existing `reportlab`, `python-docx`, `PyMuPDF`,
LangGraph, OpenAI SDK, and the standard library cover the near-term implementation.
BM25 and TF-IDF will be implemented with the standard library to avoid a new package.
Embeddings, OCR, Torch, sentence-transformers, Redis, Docker images, and model downloads
are explicitly out of scope unless a later measured requirement proves otherwise.

## M8–M10 follow-up (2026-11-22)

| Area | Result | Evidence |
| --- | --- | --- |
| M8 | Partial, additive Agent 3 implementation | Revision `0005_phase4_interviews_fairness` adds immutable interview evaluations, reasoned overrides and private generated-report metadata. The API exposes evaluate, review, final-decision and PDF/DOCX/JSON download routes. |
| M9 | Partial fairness foundation | Admin-only fairness runs calculate impact ratios, suppress groups smaller than five, and persist findings below 0.8. `tests/test_phase4_fairness.py` covers suppression and counterfactual-delta arithmetic. |
| M10 | Partial hardening/documentation | The existing `/health/live`, `/health/ready`, security middleware, audit chain and pinned lockfile are retained. Added Phase 4, responsible-AI and Phase 5 handoff documentation. |

### Follow-up command results

- `.venv-local\\Scripts\\python.exe -m alembic -c backend\\alembic.ini upgrade head`: applied `0005_phase4_interviews_fairness` successfully on local SQLite.
- `.venv-local\\Scripts\\python.exe -m compileall -q backend\\app`: passed.
- `.venv-local\\Scripts\\python.exe -m ruff check backend\\app`: passed after formatting.
- `.venv-local\\Scripts\\python.exe -m pytest -q tests\\test_phase4_fairness.py`: **2 passed**.

The full suite was started after the focused checks but its completion summary was not available from this session, so no full-suite pass count or coverage is claimed. Report rendering integration, complete evaluation harnesses, synthetic Phase 4 corpus, dashboard pages, counterfactual execution through the live scoring path, and CI coverage enforcement remain incomplete acceptance work.

## Compatibility decisions

- The existing `.env.example` uses `LLM_PROVIDER=ollama` only in the Phase 3
  local-voice section, while the new platform setting reserves `LLM_PROVIDER` for
  Agent 1 (`groq` or `azure`). The migration will move the voice-only setting to
  `VOICE_LLM_PROVIDER=ollama` and accept the legacy value for one release.
- Existing workflow threads use `hiring:{application_id}:v{version}` and
  `screening:{session_id}`. New application-level threads will accept these legacy
  identifiers when resuming an existing checkpoint; no checkpoint data is removed.
