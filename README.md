# AI-Powered HR Hiring Automation — Phases 1, 2 and 3

A connected FastAPI + Streamlit prototype for private resume intake, GroqCloud-assisted evidence evaluation, deterministic scoring, ranked HR review and persistent human approval.

AI never contacts candidates, rejects applicants or starts screening calls. Only an authorized recruiter can move a scored application to **Approved for Screening**.

## Phase 3 local voice screening

Agent 2 is integrated into the existing HR workflow using local Faster-Whisper, Ollama Qwen and Piper. It requires explicit shortlist approval, candidate consent, transcript confirmation and a final human screening decision. It does not schedule interviews or send invitations. Agent 1's working Groq provider is preserved.

See the [complete installation and usage guide](docs/phase3.md), [actual Phase 3 verification results](docs/phase3-verification.md), and [Phase 4 handoff](docs/phase4.md). Install the optional voice dependencies with the existing Python interpreter, run the explicit model setup, then use **Voice screening** in the HR dashboard. No Phase 3 provider account or card is required.

## Implementation and verification status

The code includes the Phase 1/2 workflow, database migrations, GroqCloud integration and functional dashboard. Automated tests use synthetic documents and explicitly mocked provider responses. Live GroqCloud evaluation and PostgreSQL verification are separate opt-in checks; neither is claimed successful without running it.

The local setup supports SQLite and PostgreSQL. Agent 1 uses GroqCloud with openai/gpt-oss-120b. Deployments without GroqCloud configuration show that limitation instead of fabricated scores. See [verification notes](docs/verification.md) for actual checks and remaining acceptance conditions.

## Architecture

- [Backend entrypoint](backend/app/main.py), [settings](backend/app/core/config.py), [authorization](backend/app/api/deps.py), [models](backend/app/database/models.py).
- [Requisitions](backend/app/api/requisitions.py), [private upload API](backend/app/api/resume_upload.py), [parser](backend/app/services/resume_parser.py).
- [GroqCloud adapter](backend/app/integrations/groq_cloud.py), [resume agent](backend/app/agents/resume_agent.py), [scoring validation](backend/app/services/scoring.py).
- [LangGraph](backend/app/workflows/hiring_graph.py), [evaluation/approval service](backend/app/services/evaluation_service.py), [review API](backend/app/api/evaluations.py).
- [Streamlit entrypoint](frontend/app.py), [dashboard pages](frontend/pages), [architecture details](docs/architecture.md), [API guide](docs/api.md), [Phase 3 handoff](docs/phase3.md).
- [Render + Streamlit deployment guide](docs/render-streamlit-deployment.md) and root [Render Blueprint](render.yaml).

## Windows setup

Use Python **3.11 or 3.12**. The tested local environment is Python 3.11.17. Run commands from this project folder. Activation is optional because every command explicitly selects the environment.

~~~powershell
py -3.11 -m venv .venv-local
& .\.venv-local\Scripts\python.exe -m pip install -r backend\requirements.txt -r frontend\requirements.txt -c requirements.lock.txt
if (-not (Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
& .\.venv-local\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
~~~

Copy the generated random value into JWT_SECRET_KEY in the private .env file. JWT_SECRET remains a supported legacy alias. Never commit .env. Keep exactly one assignment per setting; real environment variables take precedence over .env.

The existing copied .venv is preserved. The application and run scripts use .venv-local.

### PostgreSQL with local Python services

Install and start Docker Desktop with Compose. Set POSTGRES_PASSWORD to a generated URL-safe password in .env. Put the same password into DATABASE_URL:

~~~dotenv
POSTGRES_USER=hr
POSTGRES_PASSWORD=YOUR_GENERATED_URL_SAFE_PASSWORD
POSTGRES_DB=hr_hiring
DATABASE_URL=postgresql+psycopg://hr:YOUR_GENERATED_URL_SAFE_PASSWORD@localhost:5432/hr_hiring
~~~

Then:

~~~powershell
docker compose up -d db
& .\.venv-local\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
& .\.venv-local\Scripts\python.exe scripts\seed_demo_users.py
~~~

### SQLite fallback for this machine

If PostgreSQL is unavailable, set the following instead. This persists local data and uses a separate durable SQLite checkpoint database:

~~~dotenv
DATABASE_URL=sqlite:///./hr_hiring.sqlite3
CHECKPOINT_SQLITE_PATH=./storage/checkpoints.sqlite3
~~~

~~~powershell
& .\.venv-local\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
& .\.venv-local\Scripts\python.exe scripts\seed_demo_users.py
~~~

For the original unversioned Phase 1 database only: back it up, confirm it matches the original Phase 1 schema, stamp 0001_phase1, then upgrade head. Do not stamp a fresh database or an unknown schema. This workspace's original database was backed up and upgraded.

### Start and stop both local services

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\stop_local.ps1
~~~

The start command applies migrations, launches hidden service processes, records their process IDs and waits for readiness. The stop command verifies the recorded processes belong to this project before stopping them. Logs are private files under storage.

Or use two terminals:

~~~powershell
& .\.venv-local\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
~~~

~~~powershell
& .\.venv-local\Scripts\python.exe -m streamlit run frontend\app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
~~~

- HR dashboard: http://127.0.0.1:8501
- Swagger: http://127.0.0.1:8000/docs
- Health: http://127.0.0.1:8000/health
- Database readiness: http://127.0.0.1:8000/ready

VS Code tasks are also included: **Run hiring agent (local)**, **Run HR dashboard**, and **Run hiring workspace**. Open this project folder in VS Code to discover them. The interpreter setting points to .venv-local.

### Full Docker Compose deployment

Configure .env first, including JWT_SECRET_KEY and POSTGRES_PASSWORD. Then:

~~~powershell
docker compose up --build -d
docker compose exec backend python scripts/seed_demo_users.py
docker compose logs -f backend frontend
~~~

The backend container runs migrations before startup. PostgreSQL data and private resumes use named volumes. Host ports bind only to loopback. The frontend receives only its backend URL, not backend secrets. No demo users are created automatically during application startup.

The Docker CLI is unavailable in the current environment; image build/run verification remains pending.

## GroqCloud configuration

Agent 1 uses the existing OpenAI SDK against GroqCloud. Put the following in your private .env file or process environment:

~~~dotenv
GROQ_API_KEY=YOUR_PRIVATE_GROQ_KEY
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=openai/gpt-oss-120b
GROQ_TIMEOUT_SECONDS=45
GROQ_MAX_RETRIES=2
~~~

Only GROQ_API_KEY supplies provider credentials; Azure and OPENAI_API_KEY credentials are not used. The base URL must be the official Groq endpoint above. No Azure resource, deployment or API version is required. Both plain KEY=value lines and export KEY=value lines are supported in .env; process environment variables take precedence. Restart the backend after editing .env. Missing credentials leave Phase 1 usable and evaluation returns HTTP 503 with the missing setting names.

The existing prompts, approved rubrics, numeric source references, strict Pydantic response schema, Python weighted scoring, persisted evaluation fields and human-approval workflow are preserved. The OpenAI SDK structured parse interface sends a strict JSON schema supported by openai/gpt-oss-120b. The old adapter module remains an import compatibility shim; it makes no Azure calls.

Invalid keys or denied access fail without retry. Rate limits, connection errors, timeouts and transient API failures use bounded retries with SDK retries disabled. Numeric Retry-After values are respected within the existing eight-second delay bound; longer waits return a clear rate-limit error for a later manual retry. Raw provider responses, keys and resume content are excluded from public errors and provider logs. Failed requests never fabricate or persist scores.

References: [Groq OpenAI compatibility](https://console.groq.com/docs/openai), [Groq structured outputs](https://console.groq.com/docs/structured-outputs), [Groq error codes](https://console.groq.com/docs/errors), [Groq rate limits](https://console.groq.com/docs/rate-limits).

## Demo accounts and synthetic data

The local seed command creates:

| Account | Role |
| --- | --- |
| recruiter@demo.test | HR recruiter |
| admin@demo.test | Administrator |
| interviewer@demo.test | Reserved interviewer role |

For new accounts, DEMO_PASSWORD can supply a local-only password of at least 12 characters. Otherwise the seed generates and prints a random password once. Existing passwords are preserved. There is no public registration endpoint. Administrators create accounts through POST /api/users. Administrators inspect data and manage users; only recruiters approve progression.

[Synthetic resumes](sample_data/synthetic_resumes) include normal, incomplete, scanned, encrypted, corrupt, duplicate, unsupported and injection examples. [Additional candidates](sample_data/phase2_resumes) include table-based formatting. [Three job descriptions](sample_data/jobs.json) cover backend development, data analysis and frontend development.

~~~powershell
& .\.venv-local\Scripts\python.exe scripts\generate_synthetic_resumes.py
& .\.venv-local\Scripts\python.exe scripts\generate_phase2_data.py
~~~

With the API running and DEMO_PASSWORD set to the actual seeded recruiter password:

~~~powershell
& .\.venv-local\Scripts\python.exe scripts\seed_demo_pipeline.py
~~~

To create genuine evaluated pending examples and one explicitly approved synthetic example:

~~~powershell
& .\.venv-local\Scripts\python.exe scripts\seed_demo_pipeline.py --evaluate --approve-first
~~~

This last command requires working GroqCloud. It prints the first evaluation's evidence and asks the human operator to type APPROVE before submitting a decision. A failed provider never generates a score or an approval.

## Recruiter workflow

1. Sign in with the seeded recruiter account.
2. Open **Job requisitions**. Save an approved job description, role-specific criteria and weights totaling 100%.
3. Open **Resume uploads**. Select the job and upload PDF/DOCX files. Each file receives its own outcome.
4. Select parsed applications and run evaluation. GroqCloud failures are shown clearly.
5. Open **Candidate rankings** to compare scores, criterion findings, source passages and uncertainties.
6. Open **Candidate review**. Review evidence, acknowledge your review and approve, decline or hold.
7. Approval persists **Approved for Screening**. No calls, invitations or emails occur.

A hold is an immutable decision on that evaluation. Request a **new evaluation version** to reopen review after resolving the uncertainty. Closed/approved applications cannot be reset by an AI evaluation. Existing evaluations remain queryable by version.

## API examples

Swagger documents all endpoints and response schemas. First use POST /api/auth/login, then paste its access_token into **Authorize** as the bearer token value.

PowerShell examples below use a local credential prompt rather than recording passwords in command history:

~~~powershell
$api = 'http://127.0.0.1:8000'
$credential = Get-Credential -UserName 'recruiter@demo.test' -Message 'Local recruiter login'
$loginBody = @{email=$credential.UserName; password=$credential.GetNetworkCredential().Password} | ConvertTo-Json
$auth = Invoke-RestMethod -Method Post -Uri "$api/api/auth/login" -ContentType 'application/json' -Body $loginBody
$headers = @{Authorization="Bearer $($auth.access_token)"}
$jobBody = @{job_title='Backend Python Developer'; job_description='Develop Python and FastAPI REST APIs using PostgreSQL, with tests and documented project delivery.'; required_skills=@('Python','FastAPI','PostgreSQL')} | ConvertTo-Json -Depth 10
$job = Invoke-RestMethod -Method Post -Uri "$api/api/requisitions" -Headers $headers -ContentType 'application/json' -Body $jobBody
$upload = curl.exe --silent --show-error -X POST "$api/api/requisitions/$($job.requisition_id)/resumes" -H "Authorization: Bearer $($auth.access_token)" -F "files=@sample_data/synthetic_resumes/01_ayesha_raza.pdf" | ConvertFrom-Json
$appId = $upload.results[0].application_id
$run = Invoke-RestMethod -Method Post -Uri "$api/api/applications/$appId/evaluate" -Headers $headers -ContentType 'application/json' -Body '{}'
$evaluation = Invoke-RestMethod -Uri "$api/api/applications/$appId/evaluation" -Headers $headers
$evaluation | ConvertTo-Json -Depth 20
(Invoke-RestMethod -Uri "$api/api/requisitions/$($job.requisition_id)/rankings" -Headers $headers).items | Format-Table candidate_name,overall_score,current_status
~~~

After actually reviewing the returned evidence:

~~~powershell
$confirmation = Read-Host 'Type APPROVE after reviewing the evidence'
if ($confirmation -eq 'APPROVE') {
    $decision = @{evaluation_id=$evaluation.evaluation_id; decision='approve'; reason='HR reviewed the job-related evidence and uncertainties.'} | ConvertTo-Json
    Invoke-RestMethod -Method Post -Uri "$api/api/applications/$appId/shortlist-decision" -Headers $headers -ContentType 'application/json' -Body $decision
}
Invoke-RestMethod -Uri "$api/api/applications/$appId/history" -Headers $headers
~~~

Decline and hold require a nonempty reason. Protected routes enforce ownership in the backend, including candidates, audit history, rankings and approvals. Unauthorized resources return 404 to avoid exposing their existence.

## Scoring and durability

The LLM returns criterion findings with numeric references to server-numbered resume lines. Python retrieves the exact original passages, validates exact rubric keys, finite scores between 0 and 100, source passages and redacted-text offsets, then calculates:

~~~text
overall_score = sum(criterion_score * approved_weight / 100)
~~~

A missing finding means **not established by this document**, not proven incompetence. Keyword repetition earns no extra credit. Names, contacts and labeled protected details are minimized before the provider request, and instruction-like content is excluded. These controls are defense in depth; human review remains necessary.

Each job edit creates an immutable rubric/job snapshot. Each reevaluation gets a new version. A partial unique index permits only one active evaluation per application. Decisions are unique per evaluation and stage.

LangGraph threads use hiring:<application UUID>:v<evaluation version>. PostgreSQL uses PostgresSaver; SQLite development uses SqliteSaver in a separate private database. The graph persists the evaluation, pauses with interrupt, and resumes with Command only after the authorized decision API validates access and the current rubric. Checkpoints contain IDs and bounded evidence, not complete resume documents or secrets.

Source references avoid unreliable model-generated quotations when PDFs wrap lines or use unusual spacing. Stored evidence still contains the exact source text and offsets; invalid, out-of-range or excessively broad references are rejected within bounded retries. References crossing excluded personal or instruction-like content are also rejected. Evaluation errors distinguish missing configuration, unavailable GroqCloud requests, unreadable documents and unverified evidence. A failed attempt can be retried on the existing application without uploading the document again.

Database claims, expected-status comparisons and transaction locks prevent concurrent scoring/approval conflicts. A crashed processing claim expires after 30 minutes. If a business evaluation commits before its checkpoint is saved, the workflow reconstructs the pause from the existing evaluation without calling the provider again.

## Security and retention

- Argon2id password hashing; legacy scrypt hashes upgrade on successful login.
- Expiring JWTs; logout revokes all currently issued tokens for that account using a persisted token version.
- Database ownership checks on every candidate/requisition resource.
- Private UUID-based file paths, extension/signature/MIME/size checks, bounded DOCX expansion and no macro execution.
- Tamper-evident append-only audit chain with cross-worker serialization.
- Validation errors exclude raw request inputs; logs exclude resumes, contact data and secrets.
- CANDIDATE_RETENTION_DAYS defaults to 365. DELETE /api/applications/{id} requires an authorized owner/admin and an expired closed application by default.
- Explicit require_expired=false performs an authorized deletion request immediately. It removes resume files, extracted content, evaluations, decisions and workflow checkpoints; shared candidates remain until their final application is deleted.
- Audit records retain pseudonymous UUIDs without resume text or contact data. They intentionally do not use cascading application foreign keys, preserving chain integrity after deletion.

Preview or apply retention cleanup:

~~~powershell
& .\.venv-local\Scripts\python.exe scripts\purge_expired.py --admin-email admin@demo.test
& .\.venv-local\Scripts\python.exe scripts\purge_expired.py --admin-email admin@demo.test --apply
~~~

Local TLS, encrypted disks/backups, authentication rate limiting and organizational privacy review remain deployment responsibilities before use with real candidate data.

## Tests and checks

~~~powershell
& .\.venv-local\Scripts\python.exe -m pytest -q
& .\.venv-local\Scripts\python.exe -m ruff check backend frontend scripts tests
& .\.venv-local\Scripts\python.exe -m compileall -q backend\app frontend scripts
& .\.venv-local\Scripts\python.exe -m pip check
& .\.venv-local\Scripts\python.exe -m alembic -c backend\alembic.ini check
~~~

The tests cover intake, relationships, authentication/authorization, structured provider retries, score math, evidence traceability, injection, irrelevant personal-detail changes, persistence, duplicate prevention, approvals, genuine disk checkpoints, recovery after reconstruction, migration upgrades/downgrades and Streamlit login/navigation. The API acceptance path creates a job, uploads a resume, evaluates using a mocked LLM, ranks the application, records human approval and checks that no screening/outreach records exist.

The fairness-oriented test verifies that changed irrelevant personal details are removed and equivalent qualification input reaches the mocked provider. It is a regression test for that control, not proof of fairness of a live model across populations.

For the real GroqCloud test, configure GroqCloud in .env, then explicitly opt in:

~~~powershell
$env:RUN_LIVE_GROQ='1'
& .\.venv-local\Scripts\python.exe -m pytest -q tests\test_resume_scoring.py -k optional_live_groq
Remove-Item Env:\RUN_LIVE_GROQ
~~~

For real PostgreSQL, set TEST_POSTGRES_URL in the private .env file to a PostgreSQL SQLAlchemy URL with permission to create a temporary schema:

~~~powershell
& .\.venv-local\Scripts\python.exe -m pytest -q tests\test_postgres.py
~~~

That test runs real migrations and a durable PostgresSaver review/resume in an isolated temporary schema, then removes that schema. It does not call GroqCloud. Without the required opt-ins, live tests are explicitly skipped.

The requirements lock records the tested Python 3.11 Windows dependency set. Python 3.12/Linux Docker deployment still requires its own runtime verification. Local voice screening is implemented in Phase 3; OCR, calendar and email are outside this implementation. See the [Phase 3 guide](docs/phase3.md).
