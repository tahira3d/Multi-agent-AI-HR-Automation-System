# Phase 3 implementation and verification

Verified on 20 November 2026 in the existing application. Phase 3 source is deployed, its additive migration is applied, and FastAPI, Streamlit and local Ollama are running. Agent 1's Groq integration, scoring prompts, evaluation contract and private environment file were preserved.

## Delivered behavior

Existing HR shortlist approval gates a source-linked question plan and session creation. Candidate access uses an expiring session-scoped code and an isolated Streamlit route. Explicit consent precedes microphone controls and transcription. Local Piper speaks questions; Faster-Whisper transcribes discrete microphone recordings; candidates confirm or correct the original recognition; Qwen selects a bounded adaptive action within approved topics. Reports contain attributed candidate statements and transcript references. Completion pauses for an authorized, acknowledged HR review. No automatic hiring decision, scheduling or invitation is performed.

The implementation includes durable session/turn/command records, persistent LangGraph checkpoints, request replay protection, processing leases, pause/recovery, consent decline, human assistance and rescheduling routes, transient audio processing, retention cleanup and a Phase 4 approval handoff. The existing candidate, application, requisition, evaluation, approval and audit identifiers are reused.

## Automated and live checks

| Check | Actual result |
| --- | --- |
| Final full Pytest suite | 231 passed, 2 skipped, 0 failed; 128.09 seconds |
| Ruff | All checks passed |
| Installed dependency consistency | No broken requirements |
| Live SQLite migration | 0002_phase2 to 0003_phase3 succeeded |
| Alembic schema comparison | No new upgrade operations detected |
| SQLite foreign keys | Check passed |
| Existing records | Business table counts matched the backup; original audit hashes preserved; 1 normal login audit event appended after startup |
| Existing overview | Authenticated /api/dashboard returned HTTP 200 |
| New screening list, eligibility, local readiness | Authenticated HTTP 200 |
| Unauthenticated screening list, eligibility, readiness | HTTP 401 |
| Backend health/readiness/API docs and Streamlit health | HTTP 200 |
| Browser rendering | Existing sign-in screen rendered successfully |
| Editor diagnostics in new API/service/frontend modules | No errors reported |
| PostgreSQL migration | DDL compiled offline; no live PostgreSQL connection tested |

The two skipped tests require explicit opt-in to live Groq and a real PostgreSQL server. This run did not call a paid or hosted model. Existing Agent 1 regression tests passed; a new live Groq call was not performed. Tests cover eligibility, consent, audio decoding/limits/silence, mocked STT/TTS, approved source planning, follow-ups, context preservation, report references, corrections, token scope, HR approval, application transitions, disk reopen, interruption/retry, model failure, injection attempts, Urdu handling, retention, request duplicates and the Agent 1 scoring-to-screening API bridge.

See [machine-readable automated results](../storage/evaluations/phase3/automated-checks.json), [live checks](../storage/evaluations/phase3/live-smoke.json), [deployment checks](../storage/evaluations/phase3/deployment.json), and [compiled PostgreSQL migration](../storage/evaluations/phase3/postgres-migration.sql).

## Actual local speech measurements

Installed and exercised: Faster-Whisper 1.2.1 base CPU INT8, Piper 1.8.0 with en_US-lessac-medium, Ollama 0.40.16 with qwen2.5:1.5b. Model assets are project-local. The initial package/model installation required downloads; the demo restricted Python sockets to loopback.

Three synthetic English recordings contained 41 reference words. The measured aggregate WER was 7.32% (3 word errors). Technical-name recognition was imperfect: the strict FastAPI and PostgreSQL matches were missed in this run. Python and JSON were matched. Candidate transcript correction remains necessary.

| Synthetic sample | STT latency | TTS latency | Word errors |
| --- | ---: | ---: | ---: |
| python_project | 5.9169 s | 5.6691 s | 2/14 |
| authentication | 2.5584 s | 0.3515 s | 1/14 |
| availability | 2.7369 s | 0.2485 s | 0/13 |

The real local bounded Qwen probe succeeded in 11.5819 seconds. These are small synthetic, same-system speech checks, not independent human, accent, noisy-room or Urdu benchmarks.

Speech process peak RSS: 570.31 MiB. Full demonstration Python-process peak RSS: 824.25 MiB. A separate synthetic Qwen probe sampled a 1102.95 MiB maximum across the project Ollama process tree (7 samples). These are separate process measurements; they are not a combined system peak or certification on physical 8 GB hardware. The inspected machine has approximately 23.78 GB physical RAM and an Intel i5-10310U with integrated graphics, without NVIDIA/CUDA.

See [speech results](../storage/evaluations/phase3/voice/results.json), [reusable labeled audio manifest](../storage/evaluations/phase3/voice/synthetic-manifest.json), and [sampled Ollama memory](../storage/evaluations/phase3/ollama-active-memory.json).

## Fixed versus adaptive end-to-end demonstration

Both scenarios used synthetic candidates in a separate SQLite database. Agent 1's provider was explicitly mocked, while its real parser/scoring/source-validation and approval APIs ran. Agent 2 used real local Piper, Whisper and Qwen. The explicit --simulate-hr option supplied simulated authenticated reviewer decisions; these are not actual human hiring judgments. No live application was shortlisted or advanced by the demonstration.

| Measured quantity | Fixed baseline | Adaptive resume-aware |
| --- | ---: | ---: |
| Base approved topics | 7 | 7 |
| Total questions answered | 7 | 9 |
| Follow-ups | 0 | 2 |
| Approved-question coverage | 100% | 100% |
| Exact repeated-question rate | 0% | 0% |
| Unmatched report quotation rate | 0% | 0% |
| Average automated turn latency | 2.1547 s | 4.3874 s |

Both reports reached the simulated HR-approved interview handoff. Coverage includes addressed or explicitly unresolved topics; this demo answered all topics. Exact quote matching is a mechanical traceability check, not a human semantic unsupported-fact score. Relevant-follow-up quality, semantic factual consistency and HR correction rate need a human-labeled study and remain unmeasured. The supplied evaluator and annotation template accept real reviewer labels without inventing missing scores.

See [demo measurements](../storage/evaluations/phase3/demo/results.json), [fixed report](../storage/evaluations/phase3/demo/fixed-report.json), [adaptive report](../storage/evaluations/phase3/demo/adaptive-report.json), [fixed transcript](../storage/evaluations/phase3/demo/fixed-transcript.json), and [adaptive transcript](../storage/evaluations/phase3/demo/adaptive-transcript.json).

## Manual checks and limits

- Grant localhost microphone permission and complete a turn with a physical microphone. Automated UI checks verified consent gating, playback controls, microphone enablement, candidate isolation and HR acknowledgment, but did not operate laptop microphone hardware or confirm physical speaker playback.
- English is the working voice baseline. Urdu/automatic language STT paths and display are implemented, but Urdu recognition quality is not benchmarked. The installed English Piper voice does not support Urdu; the interface identifies the limitation and offers a human-assisted route.
- Continuous streaming, WebRTC and interruption during live audio streaming are outside this turn-by-turn MVP.
- The existing deployment uses SQLite. PostgreSQL-compatible models and offline DDL were checked; live PostgreSQL persistence/concurrency was not exercised.
- Human semantic evaluation and real HR correction measurements remain pending. The synthetic demo does not establish production hiring validity.

## Backups, installation and execution

Before deployment, the live database and checkpoint database were backed up under storage/backups/phase3-before-20261120T172318Z, together with original copies of modified existing source files and SHA-256 source metadata. Table counts were unchanged by migration and the private environment file was preserved. After startup, business table counts still matched and all original audit hashes remained intact; 1 normal login event had been appended. The database backup location and file manifest are recorded in the deployment result. No destructive migration was applied.

The local voice packages, Whisper/Piper weights, English voice model card, portable CPU Ollama runtime and quantized Qwen weights are installed in this project. The private environment file was not overwritten. Non-secret configuration examples and installation/troubleshooting instructions are in the [Phase 3 guide](phase3.md). Re-run commands from the project root:

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_local.ps1
& .\.venv-local\Scripts\python.exe -m pytest -q
& .\.venv-local\Scripts\python.exe scripts\measure_voice.py --synthetic --output-dir storage\evaluations\new-voice-run
& .\.venv-local\Scripts\python.exe scripts\demo_local_screening.py --simulate-hr --output-dir storage\evaluations\new-demo-run
~~~

Use a fresh demo output folder; the script refuses to overwrite an existing demo database. The dashboard runs at http://127.0.0.1:8501 and API documentation at http://127.0.0.1:8000/docs. Sign in as an existing authorized recruiter, complete the existing Agent 1 shortlist approval, then open Voice screening, approve the question plan and obtain a private candidate code. Candidate consent and a final real HR decision are still required for actual applications. [Phase 4 notes](phase4.md) describe the handoff without scheduling or invitations.

## Complete source file list (40 files)

This list includes modified existing files and newly added source, tests and documentation. Installed model binaries, dependency-environment files, generated measurements and safety backups are separate runtime assets.

- [.env.example](../.env.example)
- [README.md](../README.md)
- [backend/alembic/versions/0003_phase3_local_screening.py](../backend/alembic/versions/0003_phase3_local_screening.py)
- [backend/app/api/screening_limits.py](../backend/app/api/screening_limits.py)
- [backend/app/api/screenings.py](../backend/app/api/screenings.py)
- [backend/app/core/screening_config.py](../backend/app/core/screening_config.py)
- [backend/app/database/models.py](../backend/app/database/models.py)
- [backend/app/database/screening_models.py](../backend/app/database/screening_models.py)
- [backend/app/integrations/local_voice.py](../backend/app/integrations/local_voice.py)
- [backend/app/main.py](../backend/app/main.py)
- [backend/app/schemas/screening.py](../backend/app/schemas/screening.py)
- [backend/app/services/retention.py](../backend/app/services/retention.py)
- [backend/app/services/screening_retention.py](../backend/app/services/screening_retention.py)
- [backend/app/services/screening_service.py](../backend/app/services/screening_service.py)
- [backend/app/workflows/hiring_graph.py](../backend/app/workflows/hiring_graph.py)
- [backend/app/workflows/screening_graph.py](../backend/app/workflows/screening_graph.py)
- [backend/requirements-voice.lock.txt](../backend/requirements-voice.lock.txt)
- [backend/requirements-voice.txt](../backend/requirements-voice.txt)
- [docs/api.md](../docs/api.md)
- [docs/architecture.md](../docs/architecture.md)
- [docs/phase3-verification.md](../docs/phase3-verification.md)
- [docs/phase3.md](../docs/phase3.md)
- [docs/phase4.md](../docs/phase4.md)
- [frontend/app.py](../frontend/app.py)
- [frontend/pages/screening_review.py](../frontend/pages/screening_review.py)
- [frontend/pages/voice_screening.py](../frontend/pages/voice_screening.py)
- [sample_data/phase3/review-template.json](../sample_data/phase3/review-template.json)
- [scripts/demo_local_screening.py](../scripts/demo_local_screening.py)
- [scripts/download_ollama_cpu.py](../scripts/download_ollama_cpu.py)
- [scripts/evaluate_screening_reports.py](../scripts/evaluate_screening_reports.py)
- [scripts/inspect_voice_runtime.py](../scripts/inspect_voice_runtime.py)
- [scripts/install_ollama_portable.ps1](../scripts/install_ollama_portable.ps1)
- [scripts/measure_voice.py](../scripts/measure_voice.py)
- [scripts/purge_screenings.py](../scripts/purge_screenings.py)
- [scripts/setup_local_voice.py](../scripts/setup_local_voice.py)
- [scripts/start_local.ps1](../scripts/start_local.ps1)
- [tests/test_local_voice.py](../tests/test_local_voice.py)
- [tests/test_screening_metrics.py](../tests/test_screening_metrics.py)
- [tests/test_screenings.py](../tests/test_screenings.py)
- [tests/test_voice_frontend.py](../tests/test_voice_frontend.py)
