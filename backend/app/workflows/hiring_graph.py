"""Durable Phase 2 graph. Checkpoints contain IDs and bounded evidence, never full resumes."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class HiringState(TypedDict, total=False):
    application_id: str
    actor_id: str
    resume_file_id: str
    evaluation_version: int
    rubric_version: int
    request_key: str
    evaluation_id: str
    evaluation: dict[str, Any]
    status: str
    error: str
    missing_settings: list[str]
    decision: dict[str, Any]


def workflow_thread_id(application_id: str, version: int) -> str:
    return f"hiring:{application_id}:v{version}"


def build_screening_subgraph(checkpointer=None):
    """Explicit Phase 3 extension; preserve the completed Phase 2 approval history."""
    from app.workflows.screening_graph import build_screening_graph
    return build_screening_graph(checkpointer)


def build_hiring_graph(operations: Any, checkpointer: Any):
    graph = StateGraph(HiringState)
    steps = ("validate_application", "parse_resume", "validate_extracted_content", "extract_evidence",
             "evaluate_rubric", "calculate_score", "persist_evaluation")
    for name in (*steps, "mark_manual_review", "record_decision", "update_status"):
        graph.add_node(name, getattr(operations, name))

    def await_recruiter(state: HiringState) -> dict:
        return {"decision": interrupt({"application_id": state["application_id"],
            "evaluation_id": state["evaluation_id"], "rubric_version": state["rubric_version"],
            "required_action": "authorized_recruiter_shortlist_decision"})}

    graph.add_node("await_recruiter", await_recruiter)
    graph.add_edge(START, steps[0])

    def route_to(next_node):
        return lambda state: "mark_manual_review" if state.get("error") else next_node

    for current, following in zip(steps, steps[1:]):
        graph.add_conditional_edges(current, route_to(following),
                                    {following: following, "mark_manual_review": "mark_manual_review"})
    graph.add_conditional_edges("persist_evaluation", route_to("await_recruiter"),
        {"await_recruiter": "await_recruiter", "mark_manual_review": "mark_manual_review"})
    graph.add_edge("mark_manual_review", END)
    graph.add_edge("await_recruiter", "record_decision")
    graph.add_edge("record_decision", "update_status")
    graph.add_edge("update_status", END)
    return graph.compile(checkpointer=checkpointer)


@contextmanager
def persistent_checkpointer(settings: Any) -> Iterator[Any]:
    url = getattr(settings, "checkpoint_database_url", None)
    if not url and str(settings.database_url).startswith("postgresql"):
        url = settings.database_url
    if url:
        from langgraph.checkpoint.postgres import PostgresSaver
        conninfo = str(url).replace("postgresql+psycopg://", "postgresql://")
        with PostgresSaver.from_conn_string(conninfo) as saver:
            saver.setup()
            yield saver
    else:
        from langgraph.checkpoint.sqlite import SqliteSaver
        path = Path(settings.checkpoint_sqlite_path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), check_same_thread=False)
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            saver = SqliteSaver(connection)
            saver.setup()
            yield saver
        finally:
            connection.close()


def delete_workflow_checkpoints(settings: Any, application_id: str, versions: int) -> None:
    with persistent_checkpointer(settings) as saver:
        for version in range(1, versions + 1):
            saver.delete_thread(workflow_thread_id(application_id, version))
