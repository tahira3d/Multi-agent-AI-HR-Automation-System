"""Reproduce report traceability metrics and aggregate supplied human labels.
Human correction/relevance/semantic factuality are not invented from automation.
"""
import argparse
import json
from pathlib import Path


def evaluate(records):
    matches = total = corrections = reviewed = relevant = followups = unsupported = claims = 0
    for record in records:
        transcript = record['transcript']
        transcript = transcript.get('items', []) if isinstance(transcript, dict) else transcript
        answers = {row['answer']['answer_id']: row['answer']['corrected_text'] if row['answer'].get('corrected_text') is not None else row['answer']['transcript_text']
                   for row in transcript if row['answer'].get('response_status') == 'confirmed'}
        for evidence in record['report'].get('evidence', []):
            total += 1
            matches += answers.get(evidence.get('answer_id')) == evidence.get('quote')
        label = record.get('requires_report_correction')
        if isinstance(label, bool):
            reviewed += 1
            corrections += label
        count, good = record.get('followups_reviewed'), record.get('relevant_followups')
        if isinstance(count, int) and isinstance(good, int) and 0 <= good <= count:
            followups += count
            relevant += good
        count, bad = record.get('substantive_claims_reviewed'), record.get('unsupported_claims')
        if isinstance(count, int) and isinstance(bad, int) and 0 <= bad <= count:
            claims += count
            unsupported += bad
    return {'report_quotes_checked': total, 'exact_answer_quote_consistency': matches / total if total else None,
            'unmatched_report_quote_rate': (total - matches) / total if total else None,
            'human_reports_reviewed': reviewed, 'hr_correction_rate': corrections / reviewed if reviewed else None,
            'human_followups_reviewed': followups, 'relevant_followup_rate': relevant / followups if followups else None,
            'human_claims_reviewed': claims, 'unsupported_fact_rate': unsupported / claims if claims else None,
            'interpretation': 'Quote consistency is mechanical. Semantic rates require explicit human labels; null means not measured.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    records = json.loads(args.manifest.read_text('utf-8'))
    for record in records:
        for key in ['report', 'transcript']:
            if isinstance(record[key], str):
                record[key] = json.loads((args.manifest.parent / record[key]).read_text('utf-8'))
    result = evaluate(records)
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), 'utf-8')


if __name__ == '__main__':
    main()
