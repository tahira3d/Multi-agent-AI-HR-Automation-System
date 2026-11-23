from app.core.status import Status, can_transition
from app.services.fairness_service import counterfactual_delta, impact_ratio


def test_phase4_statuses_are_gated_and_reversible_to_hold():
    assert can_transition(Status.SCREENING_COMPLETED_HR_REVIEW, Status.INTERVIEW_SCORED_HR_REVIEW)
    assert can_transition(Status.INTERVIEW_SCORED_HR_REVIEW, Status.APPROVED_FOR_INTERVIEW)
    assert not can_transition(Status.SCREENING_PENDING, Status.APPROVED_FOR_INTERVIEW)
    assert can_transition(Status.APPROVED_FOR_SCREENING, Status.ON_HOLD)


def test_fairness_metrics_suppress_small_cells_and_measure_counterfactual_delta():
    assert impact_ratio({"a": (4, 5), "b": (2, 5), "small": (1, 2)}) == {"a": 1.0, "b": 0.5, "small": None}
    assert counterfactual_delta([(70.0, 69.0), (None, 80.0)])["max_absolute_score_difference"] == 1.0
