"""Phase 3 subgraph at the approved-screening handoff, using the existing checkpointer."""
from typing import TypedDict
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from app.core.config import get_settings
from app.workflows.hiring_graph import persistent_checkpointer


class ScreeningState(TypedDict):
    screening_session_id: str
    application_id: str
    phase: str
    state_version: int


def _candidate_gate(state):
    change = interrupt({'screening_session_id': state['screening_session_id'], 'gate': 'authorized_candidate_command_required'})
    return {'phase': change['phase'], 'state_version': int(change['state_version'])}


def _hr_gate(state):
    change = interrupt({'screening_session_id': state['screening_session_id'], 'gate': 'authorized_hr_decision_required'})
    return {'phase': change['phase'], 'state_version': int(change['state_version'])}


def _route(state):
    if state['phase'] in {'approved_for_interview', 'closed', 'consent_declined', 'human_requested', 'reschedule_requested', 'stopped', 'expired'}:
        return END
    return 'hr_review' if state['phase'] in {'completed', 'held'} else 'candidate_turn'


def build_screening_graph(checkpointer=None):
    graph = StateGraph(ScreeningState)
    graph.add_node('candidate_turn', _candidate_gate)
    graph.add_node('hr_review', _hr_gate)
    destinations = {'candidate_turn': 'candidate_turn', 'hr_review': 'hr_review', END: END}
    graph.add_conditional_edges(START, _route, destinations)
    graph.add_conditional_edges('candidate_turn', _route, destinations)
    graph.add_conditional_edges('hr_review', _route, destinations)
    return graph.compile(checkpointer=checkpointer)


def sync_screening_checkpoint(session_id, application_id, phase, state_version=0):
    configuration = {'configurable': {'thread_id': f'screening:{session_id}'}}
    state = {'screening_session_id': str(session_id), 'application_id': str(application_id),
             'phase': str(phase), 'state_version': int(state_version)}
    with persistent_checkpointer(get_settings()) as saver:
        graph = build_screening_graph(saver)
        snapshot = graph.get_state(configuration)
        if snapshot.values and int(snapshot.values.get('state_version', -1)) >= state_version:
            return
        graph.invoke(Command(resume={'phase': str(phase), 'state_version': state_version}) if snapshot.next else state, configuration)
