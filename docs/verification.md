# GroqCloud migration verification - 2026-11-20

Agent 1 now uses `openai/gpt-oss-120b` at `https://api.groq.com/openai/v1`, authenticated only by `GROQ_API_KEY`. The existing OpenAI SDK is reused; no package installation or version change was required. The private environment file already contained the correct Groq settings and was not modified or copied into this report.

## Observed migration results

- Baseline full suite before changes: **146 passed, 2 skipped in 29.51 seconds**.
- Migrated full suite: **182 passed, 2 skipped in 37.36 seconds**.
- Separate live GroqCloud test: **1 passed, 22 deselected in 8.43 seconds**.
- Ruff across backend, frontend, scripts and tests: **all checks passed**.
- Installed dependencies: **no broken requirements found**.
- Dummy live resume: Python/FastAPI/PostgreSQL experience and a documented inventory project; model **openai/gpt-oss-120b**, overall score **75.0/100**, **2 verified source passages**, criterion keys **required_skills** and **relevant_experience**. The existing evidence validator verified source offsets, criterion alignment and the Python weighted total.
- A separate deterministic SDK wire test retained the fixture's **82.5/100** score and existing response fields. This is a mocked provider test, separate from the live score above.
- The two full-suite skips are the deliberately excluded live Groq and PostgreSQL integrations. Groq was then verified separately; PostgreSQL was not tested.
- Prompts, rubric criteria/weights, evaluation schemas, ranking, durable approval workflow and unrelated agents were preserved. No real candidate was sent to Groq by this verification.

## Failure behavior verified

Missing Groq credentials return actionable HTTP 503 errors without falling back to Azure or OpenAI credentials. Invalid keys/access fail without retry. Rate limits use bounded backoff and numeric Retry-After; delays over eight seconds return a safe error for later retry. Timeouts, connection failures and transient API failures use the existing attempt bound, with SDK retries disabled. Raw provider errors and credentials are excluded from public errors and provider logs. API tests verify that provider failures save neither evaluations nor approvals. Evidence-validation failures retain HTTP 422.

## Reproduce

Run from the project root using the existing local environment:

~~~powershell
& .\.venv-local\Scripts\python.exe -m pytest -q
& .\.venv-local\Scripts\python.exe -m ruff check backend frontend scripts tests
& .\.venv-local\Scripts\python.exe -m pip check
$env:RUN_LIVE_GROQ='1'
& .\.venv-local\Scripts\python.exe -m pytest -q -s tests\test_resume_scoring.py -k optional_live_groq
Remove-Item Env:\RUN_LIVE_GROQ
~~~

The live test uses only synthetic information and does not persist candidate records or approvals.

## Files changed

| File | Change |
| --- | --- |
| [backend/app/integrations/groq_cloud.py](../backend/app/integrations/groq_cloud.py) | New GroqCloud adapter; existing structured parsing and evidence validation, safe errors and bounded retries. |
| [backend/app/integrations/azure_openai.py](../backend/app/integrations/azure_openai.py) | Import compatibility aliases; no Azure API calls. |
| [backend/app/agents/resume_agent.py](../backend/app/agents/resume_agent.py) | Select GroqCloud; prompt and scoring logic preserved. |
| [backend/app/core/config.py](../backend/app/core/config.py) | Groq environment settings, defaults, secret-safe representation and validation. |
| [backend/app/main.py](../backend/app/main.py) | Groq health metadata; legacy Azure flag retained as false. |
| [backend/app/services/evaluation_service.py](../backend/app/services/evaluation_service.py) | Safe Groq configuration, authentication, rate-limit and availability errors. |
| [backend/app/services/scoring.py](../backend/app/services/scoring.py) | Provider exception import and wording only; scoring calculations unchanged. |
| [backend/app/schemas/evaluation.py](../backend/app/schemas/evaluation.py) | Provider wording in comments only; schema unchanged. |
| [frontend/pages/upload_resumes.py](../frontend/pages/upload_resumes.py) | GroqCloud labels and availability guidance. |
| [backend/requirements.txt](../backend/requirements.txt) | Document continued use of the existing OpenAI SDK; no dependency/version changes. |
| [.env.example](../.env.example) | Groq key, endpoint, model, timeout, retry and live-test examples. |
| [scripts/seed_demo_pipeline.py](../scripts/seed_demo_pipeline.py) | Provider wording only. |
| [README.md](../README.md) | Groq setup, compatibility, error handling and live-test instructions. |
| [docs/api.md](../docs/api.md) | Current provider and health metadata. |
| [docs/architecture.md](../docs/architecture.md) | Current provider in diagram and references. |
| [docs/verification.md](../docs/verification.md) | This migration report; historical Azure results retained below. |
| [tests/test_config.py](../tests/test_config.py) | Groq environment/default/secret tests. |
| [tests/test_resume_scoring.py](../tests/test_resume_scoring.py) | Groq provider and optional live dummy-resume tests. |
| [tests/test_evidence_references.py](../tests/test_evidence_references.py) | Existing evidence tests run through Groq adapter. |
| [tests/test_phase2_api.py](../tests/test_phase2_api.py) | Missing configuration, safe provider failures, no persisted score on failure, and health tests. |
| [tests/test_groq_provider.py](../tests/test_groq_provider.py) | New SDK wire-contract, credentials, retry, API-error, timeout and legacy-import tests. |

## Historical verification

The following report records the previous Azure implementation and its historical checks. It does not describe the current Agent 1 provider.

# Verification report — 2026-11-09

## Actual results

- Full automated suite after the Azure evidence-reference fix: **146 passed, 2 skipped in 22.49 seconds**.
- Ruff: **all checks passed**.
- Python compilation: backend application, frontend and scripts compiled successfully.
- Dependency validation: **no broken requirements found**.
- Alembic: local schema matches the SQLAlchemy metadata.
- Compose YAML, Streamlit/project TOML and VS Code task JSON parsed successfully.
- Windows start/stop helpers were executed, corrected for PowerShell JSON array handling and verified through a real process stop/start.
- After restart, the real API retained **3 synthetic requisitions and 21 resume records**. The audit chain verified intact.
- FastAPI Swagger and Streamlit readiness returned HTTP 200.
- The real browser login and connected dashboard were checked. All five page functions also passed Streamlit AppTest checks.

A separate real Azure v1 test succeeded with GPT-4.1-mini, and the previously failed uploaded application was evaluated successfully through the running API. PostgreSQL verification remains pending. The two skipped suite tests are deliberate opt-in integrations; Azure was checked separately against the live provider.

The later uploaded-resume failure was reproduced with valid Azure connectivity. Model-generated quotations differed from the extracted document, and the exact-evidence validator correctly rejected them. The Azure wire schema now requires numeric source-line references; Python retrieves the literal source passages and verifies their offsets. The same failed application then completed through the running API with **73.75/100**, **11 verified evidence passages**, and status **Scored — HR Review**. A separate database read confirmed every saved passage matched the redacted source. The evaluation appeared in rankings; a repeated evaluation request returned the same evaluation identifier, with exactly one stored evaluation. History remained accessible and human approval remained pending.

The follow-up fix added support for export-prefixed environment assignments and Azure v1 endpoints, corrected the API mode from a model release date to v1, and made missing-setting errors actionable without recording secrets or raw provider responses.

## Acceptance mapping

| Requirement | Evidence | Result |
| --- | --- | --- |
| Secure login, roles, active account and logout | [Auth tests](../tests/test_auth.py), [security tests](../tests/test_security_permissions.py), [API tests](../tests/test_phase2_api.py) | Passed |
| Job creation/editing and configurable rubric versions | [Requisition tests](../tests/test_requisitions.py), [schema tests](../tests/test_status_contact_schemas.py), [API tests](../tests/test_phase2_api.py) | Passed |
| PDF/DOCX multi-upload, corrupt/scanned files and duplicates | [Parser tests](../tests/test_resume_parser.py), [upload tests](../tests/test_uploads.py), [Phase 1 API tests](../tests/test_api_phase1.py) | Passed; mixed 20-file batch exercised |
| Real Azure adapter implementation and bounded failures | [Scoring/provider tests](../tests/test_resume_scoring.py) | Mocked SDK tests and a separate genuine Azure v1 evaluation passed |
| Structured criterion scores, Python total, source provenance and missing evidence | [Scoring tests](../tests/test_resume_scoring.py), [source-reference tests](../tests/test_evidence_references.py) | Passed; numeric Azure citations verified on the failed upload |
| Ranked queue and secure review | [API acceptance](../tests/test_phase2_api.py), [page tests](../tests/test_frontend_pages.py) | Passed |
| HR-only approval, reasons, identity and timestamps | [Approval tests](../tests/test_approvals.py), [API tests](../tests/test_phase2_api.py) | Passed |
| Durable interruption and reconstruction | [Workflow tests](../tests/test_workflow.py), [approval tests](../tests/test_approvals.py) | Passed with real on-disk SqliteSaver |
| Duplicate prevention, stale rubric/lease conflicts and lost-checkpoint recovery | [Approval tests](../tests/test_approvals.py) | Passed |
| Migration of populated Phase 1 data and upgrade/downgrade | [Migration tests](../tests/test_migrations.py) | Passed on SQLite; checkpoint tables excluded from autogeneration |
| Ownership isolation and private-data deletion | [API tests](../tests/test_phase2_api.py), [retention tests](../tests/test_retention.py) | Passed |
| No premature calls, screening, invitations or messages | [API acceptance](../tests/test_phase2_api.py) | Approved status persisted; future-stage tables remained empty |
| Changed irrelevant personal details | [Fairness regression](../tests/test_resume_scoring.py) | Equivalent minimized qualification input; not a live-model population fairness claim |
| PostgreSQL migrations and PostgresSaver | [Opt-in PostgreSQL test](../tests/test_postgres.py) | Not run: no PostgreSQL connection configured |
| Docker images and full Compose runtime | [Compose](../docker-compose.yml), [backend image](../backend/Dockerfile), [frontend image](../frontend/Dockerfile) | Configuration parsed; build/run not performed because Docker CLI is unavailable |

## Current local runtime

- Dashboard: http://127.0.0.1:8501
- Swagger: http://127.0.0.1:8000/docs
- API version: 0.2.0
- Persistent local database: SQLite.
- AI provider configuration: Azure v1 connection verified with the configured GPT-4.1-mini deployment.
- A previously failed upload was genuinely evaluated and returned to Scored: HR Review. Human approval remains a separate authorized action.
- Synthetic intake and parsing records remain available for inspection.

The original local database was backed up before its schema upgrade. Existing demo passwords were preserved. New seed runs generate a random password for newly created accounts unless DEMO_PASSWORD is explicitly supplied.

## Remaining live acceptance conditions

The implementation is ready for these checks, but complete live Phase 1/2 acceptance remains conditional:

1. Azure connectivity and a genuine uploaded-resume evaluation are verified. Rerun the opt-in Azure test when deploying with a different resource or model deployment.
2. Configure PostgreSQL and TEST_POSTGRES_URL, then run the real PostgreSQL migration/checkpoint test.
3. Build and run Docker Compose on a machine with Docker available.
4. Run the genuine Azure-backed synthetic demonstration, review the evidence and explicitly approve one candidate while leaving another pending.

Exact commands and setup are in [README](../README.md). The connected demo seeder never creates provider scores when Azure fails. Its approval option displays the actual evaluation and waits for an explicit human confirmation.

## Tested environment

Python 3.11.17 on Windows; FastAPI 0.162.2; SQLAlchemy 2.1.8; Pydantic 2.14.3; OpenAI SDK 2.54.0; LangGraph 1.2.20; SQLite saver 3.1.3; PostgreSQL saver 3.2.0; Streamlit 1.67.0.

[Requirements lock](../requirements.lock.txt) records the full installed dependency set. Python 3.12/Linux/container runtime verification remains separate from these observed Windows results.
