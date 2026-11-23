import copy

from app.services.audit_chain import GENESIS_HASH, compute_hash, verify_chain


def build_chain(n=4):
    events, prev = [], GENESIS_HASH
    for i in range(1, n + 1):
        e = dict(seq=i, application_id="app-1", actor_id="user-1", action=f"action_{i}",
                 entity_type="application", entity_id="app-1", details={"i": i},
                 created_at=f"2026-11-02T10:0{i}:00+00:00", prev_hash=prev)
        e["hash"] = compute_hash(prev, e)
        events.append(e)
        prev = e["hash"]
    return events


def test_intact_chain_verifies():
    assert verify_chain(build_chain()) == (True, None)


def test_edited_field_detected():
    chain = build_chain()
    chain[1]["details"] = {"i": 999}
    assert verify_chain(chain) == (False, 1)


def test_edited_hash_detected():
    chain = build_chain()
    chain[2]["hash"] = "f" * 64
    ok, idx = verify_chain(chain)
    assert not ok and idx == 2


def test_deleted_middle_event_detected():
    chain = build_chain()
    del chain[1]
    assert verify_chain(chain)[0] is False


def test_reordered_events_detected():
    chain = build_chain()
    chain[1], chain[2] = chain[2], chain[1]
    assert verify_chain(chain)[0] is False


def test_tail_truncation_needs_expected_head():
    chain = build_chain()
    head = chain[-1]["hash"]
    truncated = copy.deepcopy(chain[:-1])
    assert verify_chain(truncated) == (True, None)          # undetectable alone
    assert verify_chain(truncated, expected_head=head)[0] is False  # caught with the head hash
    assert verify_chain(chain, expected_head=head) == (True, None)
