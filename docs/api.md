# REST API guide

Interactive request and response schemas: http://127.0.0.1:8000/docs. The service also exposes /openapi.json.

JWTs use the Authorization: Bearer <token> header. Login returns access_token, token_type and role. Logout revokes every currently issued token for the account.

| Method | Path | Access / behavior |
| --- | --- | --- |
| POST | /api/auth/login | Validated recruiter/admin credentials |
| POST | /api/auth/logout | Authenticated; persistent token revocation |
| GET | /api/auth/me | Current active user |
| POST | /api/users | Administrator-created accounts |
| POST | /api/requisitions | Recruiter; approved rubric snapshot |
| GET | /api/requisitions | Ownership-filtered list |
| GET | /api/requisitions/{id} | Owned requisition, or administrator read |
| PATCH | /api/requisitions/{id} | Recruiter; increments rubric version for changes |
| POST | /api/requisitions/{id}/resumes | Private multipart files; per-file results |
| GET | /api/requisitions/{id}/applications | Owned applications and parsing warnings |
| GET | /api/candidates/{id} | Only applications visible to the current user |
| POST | /api/applications/{id}/evaluate | Recruiter; real GroqCloud or explicit failure |
| GET | /api/applications/{id}/evaluation | Active result; optional version query |
| GET | /api/requisitions/{id}/rankings | Ranked review queue with evidence/version metadata |
| POST | /api/applications/{id}/shortlist-decision | Recruiter-only approve/decline/hold |
| GET | /api/applications/{id}/history | Ownership-filtered audit history |
| DELETE | /api/applications/{id} | Authorized retention/deletion operation |
| GET | /api/dashboard | Ownership-filtered counts and recent activity |
| GET | /api/audit/verify | Aggregate audit integrity, not an event dump |
| GET | /health | Process status, provider=groq and groq_configured; legacy azure_configured is false |
| GET | /ready | Database connectivity |

## Pagination and errors

Lists accept offset >= 0 and limit 1..100 (default 50). Rankings returns items, total, offset, limit. Existing requisition/application/history endpoints retain list response compatibility.

Errors use a detail property. Statuses: 401 invalid credentials, 403 role denied, 404 absent/inaccessible resource, 409 stale/concurrent/conflicting operation, 413 excessive batch, 422 invalid request/document/evidence, 503 unavailable provider/persistence. Validation responses omit raw inputs.

Upload returns HTTP 201 for a processed batch with accepted, needs_manual_review, rejected, duplicates and individual results. It does not report the entire batch successful when individual files fail. Limits: 50 files, 5 MiB per file by default, PDF/DOCX only, 100 PDF pages and bounded DOCX uncompressed size.

## Evaluation and review contracts

POST evaluate accepts an optional object with force_reevaluation and expected_rubric_version. Success returns application_id, status, workflow_thread_id and evaluation. Evidence contains criterion, source_text, explanation, evidence_type and offsets into the redacted resume text.

GET evaluation?version=1 returns historical evidence. Normal retries reuse the matching active evaluation. A changed rubric triggers a new version. A held evaluation requires force_reevaluation to start another review; finalized approved/closed applications cannot be reopened by scoring.

POST shortlist-decision requires evaluation_id and decision. Decision is approve, decline or hold; decline/hold require a reason. An identical repeat returns the existing approval. Conflicting decisions, superseded evaluations and stale rubrics return 409.

Approval stops at Approved for Screening. There is no screening-call, invitation or automatic rejection endpoint in Phase 1/2.


## Phase 3 local voice APIs

The [Phase 3 guide](phase3.md#api-contract) lists all screening routes and request semantics. The existing FastAPI OpenAPI document includes validated Pydantic input schemas.

Only a recruiter who owns the requisition can approve a question plan, start a screening, issue a scoped candidate code or record a screening decision. Candidate codes have a separate JWT audience/scope and cannot access HR APIs. Consent is checked before speech processing; transcripts are confirmed or corrected before a turn advances. Request IDs are required on mutations.

Reports carry question/answer references and self-reported statements, not hiring scores. Screening completion pauses for HR. Only an acknowledged, reasoned HR approval moves the existing Application to Approved for Interview; no scheduling or invitation is performed.
