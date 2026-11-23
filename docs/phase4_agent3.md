# Phase 4: Agent 3 and reports

Agent 3 is additive to the existing local screening workflow. `POST /api/applications/{id}/interview-evaluate` uses only confirmed candidate statements in the completed, consented screening report. It never scores accent, pace, fluency, sentiment, personality, or protected information. It returns `409` rather than manufacture an evaluation when the prerequisites or evidence are missing.

Reports are deterministic renderings of the persisted evaluation. `GET /api/applications/{id}/interview-report?format=pdf|docx|json` creates private PDF, DOCX, or JSON files labelled **SIMULATED — decision support only**, hashes them, and records report generation and download in the audit chain. A criterion override requires a reason and retains the original AI result.

The final human decision is available only from `Interview Scored - HR Review`; it can move an application to `Approved for Interview`, `On Hold`, or `Closed`. No endpoint contacts a candidate, schedules an interview, or sends email.

## Verification status

On 2026-11-22, bytecode compilation and Ruff passed in the project virtual environment after this change. The full test suite and report integration tests remain pending a complete run; they are not claimed as passed here.
