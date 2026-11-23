"""Real durable LangGraph interruption and restart tests."""
from collections import Counter
import sqlite3
from uuid import uuid4
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from app.workflows.hiring_graph import build_hiring_graph, workflow_thread_id


class Operations:
    def __init__(self, parsing_error=False):
        self.calls, self.parsing_error = Counter(), parsing_error

    def validate_application(self, state):
        return {}

    def parse_resume(self, state):
        return {"error": "scanned_pdf"} if self.parsing_error else {}

    def validate_extracted_content(self, state):
        return {}

    def extract_evidence(self, state):
        self.calls["provider"] += 1
        return {"evaluation": {"criterion_scores": {"skills": 80}}}

    def evaluate_rubric(self, state):
        return {}

    def calculate_score(self, state):
        return {}

    def persist_evaluation(self, state):
        self.calls["persist"] += 1
        return {"evaluation_id": "synthetic-evaluation", "status": "Scored \u2014 HR Review", "evaluation": {}}

    def mark_manual_review(self, state):
        return {"status": "Needs Manual Review", "evaluation": {}}

    def record_decision(self, state):
        self.calls["decision"] += 1
        return {"status": "Approved for Screening"}

    def update_status(self, state):
        return {"status": state["status"]}


def initial():
    return {"application_id": str(uuid4()), "actor_id": str(uuid4()), "resume_file_id": str(uuid4()),
            "evaluation_version": 1, "rubric_version": 1, "request_key": "test-only", "error": ""}


def test_persistent_pause_and_resume_after_restart(tmp_path):
    state, original = initial(), Operations()
    config = {"configurable": {"thread_id": workflow_thread_id(state["application_id"], 1)}}
    path = str(tmp_path / "checkpoints.sqlite3")
    connection = sqlite3.connect(path, check_same_thread=False)
    try:
        graph = build_hiring_graph(original, SqliteSaver(connection))
        result = graph.invoke(state, config)
        assert result["__interrupt__"]
        assert original.calls["decision"] == 0 and original.calls["provider"] == 1
    finally:
        connection.close()
    reconstructed = Operations()
    connection = sqlite3.connect(path, check_same_thread=False)
    try:
        graph = build_hiring_graph(reconstructed, SqliteSaver(connection))
        snapshot = graph.get_state(config)
        assert snapshot.next == ("await_recruiter",)
        assert "resume_text" not in snapshot.values
        result = graph.invoke(Command(resume={"decision": "approve"}), config)
        assert result["status"] == "Approved for Screening"
        assert reconstructed.calls["provider"] == reconstructed.calls["persist"] == 0
        assert reconstructed.calls["decision"] == 1
        graph.invoke(Command(resume={"decision": "approve"}), config)
        assert reconstructed.calls["decision"] == 1
    finally:
        connection.close()


def test_parse_failure_manual_review_without_scoring(tmp_path):
    operations = Operations(parsing_error=True)
    connection = sqlite3.connect(str(tmp_path / "manual.sqlite3"), check_same_thread=False)
    try:
        graph = build_hiring_graph(operations, SqliteSaver(connection))
        result = graph.invoke(initial(), {"configurable": {"thread_id": "manual-test"}})
        assert result["status"] == "Needs Manual Review"
        assert operations.calls["provider"] == operations.calls["decision"] == 0
    finally:
        connection.close()


def test_versions_have_distinct_stable_threads():
    app_id = str(uuid4())
    assert workflow_thread_id(app_id, 1) == workflow_thread_id(app_id, 1)
    assert workflow_thread_id(app_id, 1) != workflow_thread_id(app_id, 2)
