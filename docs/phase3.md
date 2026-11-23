# Phase 3: local candidate voice screening

Phase 3 extends the existing FastAPI, Streamlit, SQLAlchemy and LangGraph application. Agent 1 remains on its working Groq integration. Agent 2 uses local models only. Agent 3 scheduling, invitations, email and telephony are not implemented here.

The supplied Phase 3 specification is the acceptance baseline. No separate Project Development Brief was found in the inspected project.

## What runs

- Faster-Whisper base, CPU INT8: local multilingual speech recognition.
- Ollama qwen2.5:1.5b: bounded clarification/example/continue selection.
- Piper en_US-lessac-medium: English question speech.
- Streamlit microphone recording and audio playback: turn-by-turn conversation, not continuous streaming.
- Existing database and checkpointer: persistent consent, approved questions, original/corrected transcripts, factual reports and HR gates.

No provider account, paid API, card, cloud GPU or telephony subscription is required for Agent 2. Internet access is needed for initial package/model installation. Runtime Whisper loads only local files, Piper uses local ONNX weights, and Ollama is restricted to loopback. The isolated demonstration additionally blocks non-loopback Python socket connections.

## Existing architecture and integration

The handoff is the existing Application in Approved for Screening, with an immutable shortlist Approval tied to the active Evaluation and current rubric. Starting a session rechecks recruiter ownership, approval, evaluation and rubric. Resume scoring is not rebuilt.

Five new tables extend the existing Screening row: screening_sessions, screening_questions, screening_answers, screening_reports and screening_commands. Candidate, application, requisition and evaluation IDs are reused. Existing Approval and hash-chained AuditEvent entities record HR decisions. Migration 0003_phase3 adds these tables without replacing working data.

The Phase 3 subgraph reuses the existing persistent checkpointer and explicit candidate/HR interrupts. Its thread is screening:<session UUID>. The completed Phase 2 hiring thread is preserved. Checkpoints hold identifiers, phase and revision only; transcripts remain in the consent-controlled database. A committed database transition can reconcile a missed checkpoint after restart.

Slow inference runs outside database write transactions. Versioned SQL compare-and-swap claims work on SQLite and PostgreSQL. Request IDs and payload hashes prevent replaying a different operation, recording duplicate answers or repeating an HR decision. Interrupted model leases expire and recover without losing confirmed answers.

## Hardware baseline

The inspected laptop has an Intel Core i5-10310U, four cores/eight threads, about 23.78 GB physical RAM and Intel integrated graphics. No NVIDIA/CUDA toolchain was detected. The implementation still targets an 8 GB laptop baseline:

- CPU INT8 Whisper base; no PyTorch/CUDA requirement.
- Quantized 1.5B Qwen, 2,048-token context, 128 generated-token cap, two threads and CPU inference.
- One local inference at a time, shared lazy speech models and Ollama keep_alive=0.
- At most 90 seconds / 5 MiB of candidate audio per turn.
- At most two adaptive follow-ups per screening.

Actual measurements are in [verification](phase3-verification.md). Testing on this machine is not certification on physical 8 GB hardware. Swap is not counted as RAM.

## Windows installation

Run from this existing project folder using Python 3.11 or 3.12; the verified environment is the existing Python 3.11 .venv-local.

~~~powershell
& .\.venv-local\Scripts\python.exe -m pip install -r backend\requirements-voice.txt
& .\.venv-local\Scripts\python.exe scripts\setup_local_voice.py --whisper --piper
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_ollama_portable.ps1 -PullModel
~~~

The portable helper downloads CPU files from the official pinned Ollama release into storage/tools/ollama. It does not run a system-wide installer. Qwen weights are stored in storage/models/ollama. Whisper and Piper files are stored under storage/models. The downloader checks complete file sizes, records SHA-256 values, and keeps existing complete model files.

Alternatively, install Ollama from [the official website](https://ollama.com/download), start its local service and run:

~~~powershell
ollama pull qwen2.5:1.5b
ollama list
~~~

Use either the project-local service or the existing official service on port 11434. The helper preserves an already running service. Keep Ollama on loopback; candidates use the application, not the model API.

Back up the configured database before applying the new migration:

~~~powershell
& .\.venv-local\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1
~~~

After initial installation, the existing startup helper starts an installed project-local Ollama service without downloading models, then runs the existing backend/dashboard. Dashboard: http://127.0.0.1:8501. API docs: http://127.0.0.1:8000/docs.

The current workspace uses its existing SQLite database. PostgreSQL uses the same models and migration; set DATABASE_URL as already documented in the main README. Do not change a working database URL merely to enable voice screening.

For Linux/macOS, install Ollama for that OS, install the same Python requirements, run the model setup script, then run the existing Uvicorn and Streamlit commands. The Windows CPU-archive helper is Windows x64-specific.

## Configuration

The complete non-secret defaults are in [.env.example](../.env.example). Existing Agent 1 variables and private credentials are preserved. Relevant values:

~~~dotenv
VOICE_PROVIDER=local
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:1.5b
OLLAMA_TIMEOUT_SECONDS=120
STT_PROVIDER=faster_whisper
WHISPER_MODEL=base
WHISPER_MODEL_PATH=storage/models/whisper-base
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
TTS_PROVIDER=piper
TTS_VOICE=en_US-lessac-medium
PIPER_VOICE_PATH=storage/models/piper/en_US-lessac-medium.onnx
VOICE_THREADS=2
VOICE_MAX_AUDIO_BYTES=5242880
VOICE_MAX_AUDIO_SECONDS=90
SCREENING_MAX_FOLLOWUPS=2
SCREENING_DEFAULT_LANGUAGE=en
SCREENING_TOKEN_MINUTES=120
SCREENING_RETENTION_DAYS=30
SCREENING_RETENTION_AUTOMATIC=true
STREAMLIT_BASE_URL=http://127.0.0.1:8501
~~~

The provider labels describe the supported local stack; this MVP does not switch to a hosted provider. The validated inference settings are in [screening_config.py](../backend/app/core/screening_config.py). No Phase 3 API key is needed.

## HR and candidate walkthrough

1. Use Agent 1 and the existing Candidate review page to approve a scored application for screening.
2. Open Voice screening. Prepare a resume-aware question plan.
3. Review each proposed question, its source and the bounded follow-up templates. Exclude unnecessary questions. Availability and notice period remain mandatory.
4. Create the authorized session. Share the private session link and expiring access code directly with the candidate. The application sends no invitation. The candidate needs no HR account.
5. The candidate opens the link and enters the access code. Candidate access is scoped to one session and never grants HR dashboard permissions.
6. The consent page explains AI interviewing, local processing, retained text/report, discarded audio, retention and human alternatives. The microphone and speech requests stay disabled until explicit processing and transcript-retention consent.
7. Piper speaks the question. The candidate plays the audio and records an answer with the microphone. Whisper transcribes it. The candidate confirms or corrects recognition before continuing.
8. The original recognized text and correction remain distinct. Ollama can choose only a bounded follow-up action. Deterministic approved templates ground the follow-up in the current question/answer, including scoped authentication, API and database clarifications.
9. Required topics are answered or explicitly marked for human follow-up. Finishing produces a factual report and Screening Completed — HR Review.
10. HR reviews the quoted answers, original/corrected transcripts, unanswered topics, model versions and audit history. An acknowledged, reasoned HR decision can approve progression, hold, close or request clarification.

A declined consent, human request, rescheduling request or early stop leaves the application without automatic disqualification. HR must arrange the requested human-assisted alternative. No call, email, meeting or invitation is sent automatically.

The existing application label Screening Scheduled / Pending means queued local screening; Phase 3 does not schedule appointments.

## Language and microphone behavior

English voice screening is the verified baseline. Whisper accepts English or Urdu, and Unicode transcript display/correction is supported. Automatic/code-switching recognition is available in the local provider API but is not presented as a measured accuracy guarantee.

The configured Piper voice is English-only. Urdu mode does not feed Urdu text to English Piper. It offers the approved question text, Urdu recognition and a human-assisted spoken Urdu alternative. Reliable Urdu TTS and Urdu/code-switching accuracy have not been verified.

Allow microphone access in the browser's site permissions. Localhost is a browser secure context; remote microphone access requires a properly secured HTTPS deployment. If autoplay is blocked, click Play. This version records discrete answers and has no WebRTC streaming or continuous interruption handling. VAD filters recorded audio; it is not a real-time barge-in feature.

## Consent, privacy and retention

Raw candidate audio is processed transiently and never retained as a database file. Original recognition, corrections and reports require explicit retention consent. No voice biometrics, accent, personality, emotion or honesty judgments are produced.

Resume claims and transcripts are untrusted data. The model returns only an action enum; it cannot create arbitrary question wording, call tools, approve, reject, hire or schedule. The report quotes confirmed statements and source references instead of asking an LLM to invent a summary or hiring score.

Candidate tokens expire and are bound to one screening UUID/audience. The normal link keeps the access code out of the URL. Legacy access query parameters, if supplied, are removed after capture into that browser session. Candidate mode is isolated before HR navigation. Responses carrying screening data use no-store headers.

An hourly backend task removes Phase 3 content after the configured retention interval, measured from completion/stop, or start/creation for abandoned sessions. Access to expired content is blocked immediately. Existing application/evaluation records are preserved. Existing approval records and pseudonymous audit records remain under the application's approval/audit retention policy; transcript-bearing commands, reports, questions, answers and checkpoints are removed. Avoid copying full transcripts into decision reasons. Automatic cleanup can be disabled only when an authorized operator provides an equivalent retention process.

Preview or explicitly apply cleanup:

~~~powershell
& .\.venv-local\Scripts\python.exe scripts\purge_screenings.py
& .\.venv-local\Scripts\python.exe scripts\purge_screenings.py --apply
~~~

The existing authorized application deletion also removes its Phase 3 content and checkpoints. Active inference leases prevent normal deletion during processing.

## API contract

All routes are under the existing /api prefix. Candidate requests use Authorization: Bearer <session access code>. HR uses existing account JWTs.

| Route | Access / purpose |
| --- | --- |
| GET /screenings/eligible | Owned recruiter applications with a current shortlist approval |
| GET /screenings/local-status | Recruiter; installed models and local Ollama readiness |
| GET /applications/{id}/screening/plan | Owned recruiter; proposed source-linked questions |
| POST /applications/{id}/screening/start | Owned recruiter; explicitly approved questions and request_id |
| POST /screenings/{id}/access | Owned recruiter; issue a fresh expiring candidate code |
| GET /screenings | Owned recruiter session list |
| GET /screenings/{id} | Scoped candidate or authorized HR; confirmed workflow state |
| POST /screenings/{id}/consent | Explicit decision, transcript permission and language |
| GET /screenings/{id}/voice?question_id=... | Current question WAV; consent required |
| POST /screenings/{id}/audio | Bounded multipart file, question_id, request_id; consent required |
| GET /screenings/{id}/questions | Scoped approved questions and progress |
| GET /screenings/{id}/transcript | Consent-retained original/corrected turns |
| POST /screenings/{id}/answer-correction | Confirm/correct pending recognition before advancing |
| POST /screenings/{id}/control | Pause/resume/stop/human/reschedule/clarify/skip/job_question |
| POST /screenings/{id}/finish | Require coverage or explicit unresolved topics; generate report |
| GET /screenings/{id}/report | Factual, traceable report |
| POST /screenings/{id}/hr-decision | Owned recruiter only; review acknowledgment, reason and request_id |

Successful screening payloads preserve application/candidate/requisition IDs. Input validation excludes request values from public errors. Expected failures: 401/403 access, 404 absent, 409 stale/conflicting state, 410 retention expiry, 413 oversized audio, 422 invalid/unclear input, and 503 unavailable local inference/storage. Confirmed progress is retained.

## Tests, measurements and synthetic examples

~~~powershell
& .\.venv-local\Scripts\python.exe -m pytest -q
& .\.venv-local\Scripts\python.exe -m ruff check backend frontend scripts tests
& .\.venv-local\Scripts\python.exe -m pip check
& .\.venv-local\Scripts\python.exe scripts\measure_voice.py --synthetic
& .\.venv-local\Scripts\python.exe scripts\demo_local_screening.py --simulate-hr
~~~

The measurement script records real speech WER, technical-term matches, latencies and Python-process peak RSS. It saves the generated WAVs and labeled manifest. Reuse that manifest with --manifest for a fixed-audio repeat. These are synthetic, permissioned test artifacts, not stored candidate recordings.

The isolated demonstration creates a separate SQLite database and exercises real local question speech, transcription, confirmation, adaptive turns, reports and simulated authenticated HR decisions. Agent 1's provider is explicitly mocked; its real parser/scoring/source-validation and approval API run. It never calls Groq or modifies the live database. Without --simulate-hr, reports remain pending HR review. Simulated approval is not presented as actual human review.

The fixed/adaptive comparison reports coverage, exact repeated-question rate, unmatched report quotes and actual turn latency. Human semantic ratings and HR correction rate are reproducible using scripts/evaluate_screening_reports.py and the annotation template in sample_data/phase3/review-template.json. Unfilled labels remain null. Physical microphone testing, Urdu benchmarks and PostgreSQL runtime verification are reported separately when not measured. No absent result is replaced with an invented score.

## Troubleshooting

- Overview HTTP 500: the earlier stale Azure flag was fixed; the overview uses Groq configuration.
- Voice unavailable: check Local model readiness, requirements-voice.txt, model paths and Ollama port 11434. No provider API key fixes missing local models.
- Ollama download stalls: rerun the pull; completed layers are reused. The portable CPU helper avoids downloading unused CUDA/Vulkan components. Check disk space and network access.
- Windows DLL import error: use 64-bit Python and the supported Microsoft Visual C++ runtime required by the installed wheels.
- Empty/noisy recording: check microphone selection and permission, record closer to the microphone, or request human assistance. No candidate capability inference is made.
- Technical words recognized incorrectly: correct the visible transcript before confirming. The baseline measurements explicitly record imperfect technical-name recognition.
- Busy model: retry the same request; one local inference is allowed at a time.
- Browser/API timeout: refresh first. An already committed answer is recovered instead of recorded twice.
- Unexpected restart: reopen the same link. Pending confirmations persist; expired processing leases recover.
- Wrong or expired candidate link: ask the authorized recruiter for a new scoped code.
- Consent declined: use the HR human-assisted route. Do not treat the decline as an automatic rejection.
- PostgreSQL unavailable: preserve the current working SQLite configuration for this local MVP, or configure and verify PostgreSQL separately.
- Retention cleanup deferred: restore storage connectivity/configuration and inspect aggregate operational messages. Do not enable transcript collection without an enforceable retention policy.

## Phase 4 handoff

Only an authorized screening Approval moves an application to Approved for Interview. Agent 3 must recheck that status, the screening approval/audit reference and current authorization before scheduling. Phase 3 creates no Interview or Communication rows and sends no invitation. See [Phase 4 notes](phase4.md).

## Primary references and licensing

- [Ollama installation](https://ollama.com/download) and [local Chat API](https://docs.ollama.com/api/chat).
- [Faster-Whisper](https://github.com/SYSTRAN/faster-whisper): local model files, CPU INT8 and audio decoding.
- [Piper](https://github.com/OHF-Voice/piper1-gpl) and its [Python API](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md).
- [Streamlit microphone input](https://docs.streamlit.io/develop/api-reference/widgets/st.audio_input).
- Piper engine uses GPL-3.0. Review the separate voice/dataset terms in the downloaded storage/models/piper/MODEL_CARD before redistribution. No voice license is inferred from the engine license.
