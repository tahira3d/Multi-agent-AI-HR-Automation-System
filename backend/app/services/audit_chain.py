"""Tamper-evident audit chain: each event's hash covers the previous event's hash.

Pure functions, no database. `audit_service.py` stores events; this module defines how
hashes are computed and how a stored chain is verified (FR-14, NFR auditability).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping

GENESIS_HASH = "0" * 64

# Fields that are covered by the hash. Anything else on a stored row is ignored.
HASHED_FIELDS = (
    "seq",
    "application_id",
    "actor_id",
    "action",
    "entity_type",
    "entity_id",
    "details",
    "created_at",
)


def canonical_json(payload: Mapping[str, Any]) -> str:
    subset = {k: payload.get(k) for k in HASHED_FIELDS}
    return json.dumps(subset, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_hash(prev_hash: str, payload: Mapping[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(payload)).encode("utf-8")).hexdigest()


def verify_chain(
    events: Iterable[Mapping[str, Any]], expected_head: str | None = None
) -> tuple[bool, int | None]:
    """Verify events in sequence order.

    Returns (True, None) when intact, otherwise (False, index_of_first_bad_event).
    Detects edited fields, edited hashes, reordered, inserted or deleted events.
    Deleting events from the *end* leaves a valid shorter chain, so pass `expected_head`
    (the latest hash recorded elsewhere, e.g. shown on the dashboard) to catch that too.
    """
    prev = GENESIS_HASH
    count = 0
    for index, event in enumerate(events):
        count = index + 1
        if event.get("prev_hash") != prev:
            return False, index
        if compute_hash(prev, event) != event.get("hash"):
            return False, index
        prev = event["hash"]
    if expected_head is not None and prev != expected_head:
        return False, count
    return True, None
