# Phase 4 handoff: reserved scheduling agent

Phase 3 ends at an authenticated, reviewed screening Approval and Application.current_status = Approved for Interview. It does not create interviews, appointments, invitations, emails or calendar events.

Agent 3 must verify current ownership/permissions, the approved application stage and the screening approval reference. ScreeningCommand HR-decision metadata links the reviewer, reason, timestamp, Approval ID and reviewed report ID; AuditEvent preserves the pseudonymous screening UUID and approval reference. Reports/transcripts remain subject to consent and retention and may expire independently of the approval audit.

Before scheduling, obtain explicit authorization for the intended interview, validate candidate/interviewer availability and time zones, use an idempotent scheduling claim, and retain a separate human invitation gate. An LLM must not grant permissions or send invitations. A hold, closure, stopped screening, declined consent or human-assistance request is not an interview authorization.

Reuse existing candidate/application/requisition IDs and the prepared Interview/Communication entities. Do not reconstruct deleted candidate transcripts from checkpoints or audit logs. No external service account or calendar provider is configured by Phase 3.
