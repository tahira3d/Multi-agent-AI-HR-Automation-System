"""Fairness metrics isolated from scoring and candidate PII."""

from __future__ import annotations

from collections.abc import Iterable


def impact_ratio(groups: dict[str, tuple[int, int]]) -> dict[str, float | None]:
    """Selection-rate ratio to the best sufficiently sized group; small cells hide."""
    rates = {name: selected / total for name, (selected, total) in groups.items() if total >= 5}
    if not rates:
        return {name: None for name in groups}
    highest = max(rates.values())
    return {name: (round(rates[name] / highest, 4) if name in rates and highest else None) for name in groups}


def counterfactual_delta(scores: Iterable[tuple[float | None, float | None]]) -> dict[str, float | int]:
    deltas = [abs(left - right) for left, right in scores if left is not None and right is not None]
    return {"max_absolute_score_difference": round(max(deltas, default=0.0), 3), "pairs_scored": len(deltas)}
