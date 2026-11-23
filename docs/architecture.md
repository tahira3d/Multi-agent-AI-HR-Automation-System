# Phase 1/2 architecture

~~~mermaid
flowchart LR
  HR[Authenticated HR recruiter] --> UI[Streamlit dashboard]
  UI --> API[FastAPI ownership + RBAC]
  API --> Intake[Private PDF/DOCX intake]
  Intake --> DB[(SQLAlchemy application database)]
  API --> Graph[Durable LangGraph thread]
  Graph --> Minimize[Redaction + untrusted content controls]
  Minimize --> Groq[GroqCloud structured evidence]
  Groq --> Validate[Provenance + Python weighted score]
  Validate --> DB
  DB --> Pause[Scored: HR Review / interrupt]
  Pause --> Decision[Authorized human decision]
  Decision --> Handoff[Approved for Screening]
  Graph --> Checkpoint[(PostgresSaver / private SqliteSaver)]
~~~

## Trust boundaries

The browser communicates with Streamlit. Streamlit sends the user's JWT to FastAPI. Every resource handler checks backend permissions and requisition ownership; frontend navigation never grants access.

Candidate documents are untrusted data. Parsers never execute macros, and source text is minimized before GroqCloud. The provider has no candidate-contact tools. Scores must resolve to exact source passages and approved criterion keys.

The application database stores operational facts. Checkpoints store stable IDs and bounded structured evidence. A new graph instance can load the same thread after a restart. Secrets and complete resume text are excluded from checkpoints.

In PostgreSQL, LangGraph manages its checkpoint tables and migrations independently. Application Alembic autogeneration explicitly excludes those tables, so it cannot propose deleting durable workflow state.

## Data model

Users own requisitions. Candidates can have applications for multiple requisitions; the candidate/requisition pair is unique. Private resume files belong to applications. Evaluations retain versions and job-rubric snapshots, with one active evaluation. Approval decisions are unique per evaluation/stage and include reviewer identity and timestamp.

RubricVersion records capture approved job and rubric snapshots. AuditEvent uses a UUID primary key and a unique sequence for its hash chain. A mutex row serializes audit appends across workers. Pseudonymous audit references survive authorized application deletion so the chain remains verifiable.

The retained screenings/interviews/communications tables are schema preparation for future phases; no Phase 3/4 behavior executes.

## Transaction and crash boundaries

A short transaction claims an application using its prior evaluation version, status and a unique processing token. Slow model calls do not hold an application write transaction open. Persistence revalidates the rubric under a row lock and checks the processing lease in SQL.

Evaluation commit precedes the HR interrupt. If the following checkpoint is lost, the existing request key allows pause reconstruction without creating another evaluation. Approval, status and audit event commit together. Replaying the same decision returns the existing record; a conflicting decision returns 409.

A 30-minute lease bounds recovery of abandoned in-flight processing. Different evaluation versions use different thread IDs. A hold retains its decision; forced reevaluation creates a new review version.

## Primary implementation references

- [Groq structured outputs](https://console.groq.com/docs/structured-outputs)
- [OpenAI structured output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)


## Phase 3 extension

The existing approved-screening handoff opens an authorized local screening subgraph. Phase 2 threads/history remain intact.

~~~mermaid
flowchart LR
  Approval[Existing HR shortlist approval] --> Plan[HR approves source-linked questions]
  Plan --> Consent[Scoped candidate session and consent]
  Consent -->|accepted| Piper[Local Piper question speech]
  Consent -->|declined / human / reschedule| Human[Human route, no disqualification]
  Piper --> Mic[Streamlit discrete microphone answer]
  Mic --> STT[Local Whisper CPU INT8]
  STT --> Confirm[Candidate confirms or corrects original recognition]
  Confirm --> Choice[Local Qwen bounded follow-up action]
  Choice -->|approved follow-up or next question| Piper
  Choice -->|required topics addressed| Report[Extractive report with transcript references]
  Report --> Gate[Persistent HR review interrupt]
  Gate --> Decision[Authenticated HR decision and audit]
  Decision --> Handoff[Approved for Interview: Phase 4 handoff only]
~~~

The model never owns permissions or side effects. PostgreSQL/SQLite store the confirmed state and immutable command identifiers; the existing persistent checkpointer stores only IDs, phase and revision. Audio is transient, and consent-controlled text/content expires under the Phase 3 retention policy. See [full details](phase3.md).
