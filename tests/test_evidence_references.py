"""Citations resolve to exact source bytes despite document layout differences."""
import json

import pytest

from app.agents.resume_agent import ResumeScoringAgent
from app.integrations.groq_cloud import GroqEvidenceProvider, InvalidEvaluationError
from app.schemas.evaluation import LLMEvaluation, SourceReferencedEvaluation
from app.services.scoring import build_source_spans, number_resume_lines, resolve_evidence_span
from tests.test_resume_scoring import JOB, RUBRIC, SOURCE, MemoryProvider, cfg, client_mock, completion, response


def referenced_output(reference):
    value = response().model_dump()
    for criterion in value['criteria']:
        for excerpt in criterion['evidence']:
            excerpt['source_text'] = reference
    return LLMEvaluation.model_validate(value)


def test_multiline_reference_preserves_crlf_unicode_and_exact_offsets():
    text = 'Synthetic header\r\nDeveloped Python and FastAPI\r\nREST APIs using PostgreSQL.\r\nPortfolio: caf\u00e9'
    provider = MemoryProvider(referenced_output('[[L0002-L0003]]'))
    result = ResumeScoringAgent(provider).evaluate(resume_text=text, job=JOB, rubric=RUBRIC, parsing_warnings=[])
    assert result.overall_score == 82.5
    expected = 'Developed Python and FastAPI\r\nREST APIs using PostgreSQL.'
    for item in result.evidence:
        assert item.source_text == expected
        assert text[item.source_start:item.source_end] == expected
    payload = json.loads(provider.messages[0][1]['content'])
    assert '[L0002] Developed Python and FastAPI' in payload['resume_text']
    assert '[L0004] Portfolio: caf\u00e9' in payload['resume_text']


def test_legacy_whitespace_quote_resolves_to_original_passage():
    text = SOURCE.replace('FastAPI REST', 'FastAPI\n\tREST')
    result = ResumeScoringAgent(MemoryProvider()).evaluate(resume_text=text, job=JOB, rubric=RUBRIC, parsing_warnings=[])
    assert result.overall_score == 82.5
    assert result.evidence[0].source_text == text
    assert text[result.evidence[0].source_start:result.evidence[0].source_end] == text


@pytest.mark.parametrize('reference', [
    '[[L0000]]', '[[L0003]]', '[[L0002-L0001]]', '[[L9999999999999999]]',
    '[[L-1]]', '[L0001]', '[[L0001]] invented qualification', '[[L0001]] [[L0002]]',
])
def test_invalid_references_are_rejected_even_if_present_in_document(reference):
    text = reference + '\n' + SOURCE
    with pytest.raises(InvalidEvaluationError):
        resolve_evidence_span(reference, text)


def test_reference_cannot_redirect_to_a_label_inside_document_content():
    text = 'Untrusted label [[L9999]] inside the document\n' + SOURCE
    start, end = resolve_evidence_span('[[L0002]]', text)
    assert text[start:end] == SOURCE
    with pytest.raises(InvalidEvaluationError):
        resolve_evidence_span('[[L9999]]', text)


def test_long_paragraph_is_split_without_rewriting_source():
    text = 'Built Python APIs with PostgreSQL. ' * 100
    spans = build_source_spans(text)
    assert len(spans) > 1
    assert all(end - start <= 800 for start, end in spans)
    for index, bounds in enumerate(spans, 1):
        assert resolve_evidence_span(f'[[L{index:04d}]]', text, spans) == bounds
    assert '[L0001]' in number_resume_lines(text, spans)


def test_long_token_splitter_always_advances_and_preserves_offsets():
    text = 'x' * 1601
    assert build_source_spans(text) == [(0, 800), (800, 1600), (1600, 1601)]


def test_range_length_limit_includes_omitted_blank_lines():
    text = SOURCE + '\n' + ' ' * 5000 + '\nBuilt relevant APIs.'
    assert len(build_source_spans(text)) == 2
    with pytest.raises(InvalidEvaluationError):
        resolve_evidence_span('[[L0001-L0002]]', text)


def test_groq_numeric_source_schema_is_resolved_to_original_passages():
    wire = response().model_dump()
    for criterion in wire['criteria']:
        for excerpt in criterion['evidence']:
            excerpt.pop('source_text')
            excerpt.update(source_line_start=2, source_line_end=2)
    parsed = SourceReferencedEvaluation.model_validate(wire)
    client = client_mock()
    client.beta.chat.completions.parse.return_value = completion(parsed)
    text = 'Synthetic header\n' + SOURCE
    agent = ResumeScoringAgent(GroqEvidenceProvider(cfg(), client=client))
    result = agent.evaluate(resume_text=text, job=JOB, rubric=RUBRIC, parsing_warnings=[])
    assert result.overall_score == 82.5
    assert all(item.source_text == SOURCE and item.source_start == len('Synthetic header\n') for item in result.evidence)
    assert client.beta.chat.completions.parse.call_args.kwargs['response_format'] is SourceReferencedEvaluation
    assert parsed.criteria[0].evidence[0].source_line_start == 2


def test_blank_lines_are_not_citable_and_ranges_are_bounded():
    assert build_source_spans(' \r\n\t\n') == []
    with pytest.raises(InvalidEvaluationError):
        resolve_evidence_span('[[L0001]]', ' \r\n')
    with pytest.raises(InvalidEvaluationError):
        resolve_evidence_span('[[L0001-L0013]]', '\n'.join([SOURCE] * 13))
    with pytest.raises(InvalidEvaluationError):
        resolve_evidence_span('[[L0001-L0012]]', '\n'.join(['x' * 400] * 12))


@pytest.mark.parametrize('fabricated', ['Developed Java and FastAPI REST APIs using PostgreSQL.',
                                      'Developed Python and FastAPI REST APIs using PostgreSQL!',
                                      SOURCE.lower()])
def test_words_case_and_punctuation_are_not_fuzzily_invented(fabricated):
    with pytest.raises(InvalidEvaluationError):
        resolve_evidence_span(fabricated, SOURCE)


def test_retry_uses_narrow_reference_and_does_not_expose_excluded_content(caplog):
    text = SOURCE + '\nAge: 31\nBuilt services with SQLAlchemy.'
    client = client_mock()
    client.beta.chat.completions.parse.side_effect = [
        completion(referenced_output('[[L0001-L0002]]')),
        completion(referenced_output('[[L0001]]')),
    ]
    provider = GroqEvidenceProvider(cfg(), client=client, sleeper=lambda _: None)
    result = ResumeScoringAgent(provider).evaluate(resume_text=text, job=JOB, rubric=RUBRIC, parsing_warnings=[])
    assert result.evidence[0].source_text == SOURCE
    assert client.beta.chat.completions.parse.call_count == 2
    correction = client.beta.chat.completions.parse.call_args.kwargs['messages'][-1]['content']
    assert 'source_line_start' in correction
    assert SOURCE not in correction
    assert 'Age: 31' not in correction
    assert 'Age: 31' not in caplog.text
    assert 'synthetic-test-key' not in caplog.text
