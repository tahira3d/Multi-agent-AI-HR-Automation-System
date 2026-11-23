"""Metric arithmetic with synthetic labels; not a claim about model performance."""
from scripts.evaluate_screening_reports import evaluate
from scripts.measure_voice import word_errors


def test_word_error_rate_counts_insertions_deletions_and_substitutions():
    assert word_errors('I use Python', 'I use Python') == (0, 3)
    assert word_errors('I use FastAPI', 'I use fast API') == (2, 3)


def test_unmeasured_human_metrics_remain_null():
    result = evaluate([{'report': {'evidence': [{'answer_id': 'a', 'quote': 'I built APIs.'}]},
        'transcript': [{'answer': {'answer_id': 'a', 'response_status': 'confirmed', 'transcript_text': 'I built APIs.', 'corrected_text': None}}]}])
    assert result['exact_answer_quote_consistency'] == 1
    assert result['hr_correction_rate'] is None and result['unsupported_fact_rate'] is None


def test_human_annotation_arithmetic_and_unmatched_quotes():
    records = [{'report': {'evidence': [{'answer_id': 'a', 'quote': 'Invented'}]}, 'transcript': [],
        'requires_report_correction': True, 'followups_reviewed': 2, 'relevant_followups': 1,
        'substantive_claims_reviewed': 4, 'unsupported_claims': 1},
        {'report': {'evidence': []}, 'transcript': [], 'requires_report_correction': False}]
    result = evaluate(records)
    assert result['hr_correction_rate'] == 0.5 and result['unsupported_fact_rate'] == 0.25
    assert result['relevant_followup_rate'] == 0.5 and result['unmatched_report_quote_rate'] == 1
